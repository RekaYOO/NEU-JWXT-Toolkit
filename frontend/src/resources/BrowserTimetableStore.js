/*
 * Explicit browser cache for the official personal timetable.
 *
 * This is intentionally separate from localStorage settings: a timetable is
 * account-scoped business data, so it gets its own versioned IndexedDB store,
 * bounded to the current and immediately-next terms.  Selection overlays and
 * conflict results are never written here.
 */

const DB_NAME = 'neu-toolbox-browser-cache';
const DB_VERSION = 1;
const STORE_NAME = 'timetable';
const CHANNEL_NAME = 'neu-toolbox-timetable-cache';
const ENVELOPE_VERSION = 1;

const termOrder = code => {
  const match = String(code || '').match(/(20\d{2})[^0-9]+(20\d{2})[^0-9]+([12])/);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : [0, 0, 0];
};

const allowedPersonalTerms = (terms, current) => {
  const codes = [...new Set((terms || []).map(item => String(item?.code || '')).filter(Boolean))]
    .sort((a, b) => {
      const left = termOrder(b);
      const right = termOrder(a);
      return (left[0] - right[0]) || (left[1] - right[1]) || (left[2] - right[2]);
    });
  const active = String(current || (terms || []).find(item => item?.current)?.code || codes[0] || '');
  const position = codes.indexOf(active);
  return new Set([active, position >= 0 ? codes[position - 1] : codes[1]].filter(Boolean));
};

const hasIndexedDB = () => (
  typeof window !== 'undefined' && typeof window.indexedDB !== 'undefined'
);

const namespaceFor = (identity) => {
  // The namespace must not contain the raw account name. This is an identifier
  // partition, not encryption; the browser origin/profile remains the security
  // boundary and the UI never reads it before online identity confirmation.
  let hash = 2166136261;
  for (const char of String(identity || '')) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `account:${(hash >>> 0).toString(16)}`;
};

const openDatabase = () => new Promise((resolve, reject) => {
  if (!hasIndexedDB()) {
    resolve(null);
    return;
  }
  let request;
  try {
    request = window.indexedDB.open(DB_NAME, DB_VERSION);
  } catch (_error) {
    resolve(null);
    return;
  }
  request.onupgradeneeded = () => {
    const database = request.result;
    if (!database.objectStoreNames.contains(STORE_NAME)) {
      const store = database.createObjectStore(STORE_NAME, { keyPath: 'key' });
      store.createIndex('namespace', 'namespace', { unique: false });
      store.createIndex('kind', 'kind', { unique: false });
    }
  };
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => resolve(null);
  request.onblocked = () => resolve(null);
});

const transaction = (database, mode, operation) => new Promise((resolve, reject) => {
  if (!database) {
    resolve(null);
    return;
  }
  let request;
  try {
    const tx = database.transaction(STORE_NAME, mode);
    request = operation(tx.objectStore(STORE_NAME));
    tx.oncomplete = () => resolve(request?.result);
    tx.onerror = () => resolve(null);
    tx.onabort = () => resolve(null);
  } catch (_error) {
    resolve(null);
  }
});

const close = database => {
  try { database?.close?.(); } catch (_error) { /* best effort */ }
};

const postUpdate = (namespace, termCode = '', resource = 'timetable') => {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel === 'undefined') return;
  try {
    const channel = new window.BroadcastChannel(CHANNEL_NAME);
    channel.postMessage({ namespace, termCode, resource, at: Date.now() });
    channel.close();
  } catch (_error) { /* BroadcastChannel is advisory */ }
};

const browserChannel = (identity, resource, callback) => {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel === 'undefined') return () => {};
  const namespace = namespaceFor(identity);
  let channel;
  try {
    channel = new window.BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = event => {
      if (event.data?.namespace === namespace && event.data?.resource === resource) callback(event.data);
    };
  } catch (_error) {
    return () => {};
  }
  return () => {
    try { channel.close(); } catch (_error) { /* best effort */ }
  };
};

export const readBrowserTimetableCache = async identity => {
  const namespace = namespaceFor(identity);
  const database = await openDatabase();
  if (!database) return { terms: [], current: '', personal: [], viewState: null };
  const rows = await transaction(database, 'readonly', store => store.index('namespace').getAll(namespace));
  close(database);
  // A future schema must never be interpreted as an older timetable payload.
  // Ignore incompatible envelopes and let the server bootstrap repopulate it.
  const values = (Array.isArray(rows) ? rows : []).filter(item => item?.version === ENVELOPE_VERSION);
  const index = values.find(item => item.kind === 'index');
  const current = index?.payload?.current || '';
  const allowed = allowedPersonalTerms(index?.payload?.terms || [], current);
  const personal = values
    .filter(item => item.kind === 'personal' && item.payload?.term_code
      && (!allowed.size || allowed.has(String(item.payload.term_code))))
    .map(item => item.payload)
    .sort((a, b) => String(a.term_code).localeCompare(String(b.term_code)))
    .slice(-2);
  return {
    terms: index?.payload?.terms || [],
    current,
    personal,
    viewState: index?.payload?.viewState || null,
  };
};

export const writeBrowserTimetableCache = async (identity, {
  terms = [], current = '', personal = [], viewState = null,
} = {}) => {
  const namespace = namespaceFor(identity);
  const database = await openDatabase();
  if (!database) return false;
  const allowedTerms = allowedPersonalTerms(terms, current);
  const bounded = (personal || []).filter(item => (
    item?.term_code && (!allowedTerms.size || allowedTerms.has(String(item.term_code)))
  )).slice(-2);
  await transaction(database, 'readwrite', store => {
    store.put({
      key: `${namespace}:index`,
      namespace,
      kind: 'index',
      version: ENVELOPE_VERSION,
      payload: { terms, current, viewState, saved_at: new Date().toISOString() },
    });
    bounded.forEach(payload => store.put({
      key: `${namespace}:personal:${payload.term_code}`,
      namespace,
      kind: 'personal',
      termCode: String(payload.term_code),
      version: ENVELOPE_VERSION,
      payload,
    }));
    // Remove old personal variants; only current/next terms are retained.
    const request = store.index('namespace').openCursor(namespace);
    request.onsuccess = event => {
      const cursor = event.target.result;
      if (!cursor) return;
      const value = cursor.value;
      if (value.kind === 'personal' && !bounded.some(item => String(item.term_code) === value.termCode)) {
        cursor.delete();
      }
      cursor.continue();
    };
    return request;
  });
  close(database);
  bounded.forEach(payload => postUpdate(namespace, payload.term_code));
  return true;
};

export const clearBrowserTimetableCache = async identity => {
  const namespace = namespaceFor(identity);
  const database = await openDatabase();
  if (!database) return false;
  await transaction(database, 'readwrite', store => {
    const request = store.index('namespace').openCursor(namespace);
    request.onsuccess = event => {
      const cursor = event.target.result;
      if (!cursor) return;
      cursor.delete();
      cursor.continue();
    };
    return request;
  });
  close(database);
  postUpdate(namespace);
  return true;
};

export const subscribeBrowserTimetableCache = (identity, callback) => {
  return browserChannel(identity, 'timetable', callback);
};

export const readBrowserAvatarCache = async identity => {
  const namespace = namespaceFor(identity);
  const database = await openDatabase();
  if (!database) return null;
  const rows = await transaction(database, 'readonly', store => store.index('namespace').getAll(namespace));
  close(database);
  const row = (Array.isArray(rows) ? rows : []).find(item => (
    item?.kind === 'avatar' && item?.version === ENVELOPE_VERSION && item.payload?.blob
  ));
  return row?.payload || null;
};

export const writeBrowserAvatarCache = async (identity, payload) => {
  if (!payload?.blob) return false;
  const existing = await readBrowserAvatarCache(identity);
  if (payload.revision && existing?.revision === String(payload.revision)) return true;
  const namespace = namespaceFor(identity);
  const database = await openDatabase();
  if (!database) return false;
  await transaction(database, 'readwrite', store => store.put({
    key: `${namespace}:avatar`,
    namespace,
    kind: 'avatar',
    version: ENVELOPE_VERSION,
    payload: {
      token: String(payload.token || ''),
      blob: payload.blob,
      revision: String(payload.revision || ''),
      saved_at: payload.saved_at || new Date().toISOString(),
      last_checked_at: payload.last_checked_at || new Date().toISOString(),
    },
  }));
  close(database);
  postUpdate(namespace, '', 'avatar');
  return true;
};

export const clearBrowserAvatarCache = async identity => {
  const namespace = namespaceFor(identity);
  const database = await openDatabase();
  if (!database) return false;
  await transaction(database, 'readwrite', store => store.delete(`${namespace}:avatar`));
  close(database);
  postUpdate(namespace, '', 'avatar');
  return true;
};

export const subscribeBrowserAvatarCache = (identity, callback) => (
  browserChannel(identity, 'avatar', callback)
);

export const browserTimetableCacheNamespace = namespaceFor;
