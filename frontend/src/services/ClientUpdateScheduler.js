import { getClientUpdates } from './api';

const EVENT_POLL_MS = 15000;
const IDLE_POLL_MS = 30000;
const PENDING_AUTH_POLL_MS = 2500;
const FOLLOWER_CHECK_MS = 5000;
const LEASE_MS = 12000;
const schedulers = new Map();

const stableKey = value => {
  let hash = 2166136261;
  for (const character of String(value || '')) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
};

const createScheduler = identity => {
  const listeners = new Set();
  const instanceId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const namespace = stableKey(identity);
  const leaseKey = `neu-client-updates-leader:${namespace}`;
  let cursor = '';
  let timer = null;
  let leaseTimer = null;
  let inFlight = false;
  let stopped = false;
  let channel = null;

  const notify = payload => listeners.forEach(listener => {
    try { listener(payload); } catch (_error) { /* isolate page subscribers */ }
  });

  const schedule = delay => {
    window.clearTimeout(timer);
    if (!stopped) timer = window.setTimeout(tick, delay);
  };

  const readLease = () => {
    try { return JSON.parse(window.localStorage.getItem(leaseKey) || 'null'); }
    catch (_error) { return null; }
  };

  const stopLeaseHeartbeat = () => {
    window.clearTimeout(leaseTimer);
    leaseTimer = null;
  };

  const renewLease = () => {
    stopLeaseHeartbeat();
    if (stopped || !channel) return;
    const lease = readLease();
    if (lease?.id !== instanceId) return;
    try {
      window.localStorage.setItem(leaseKey, JSON.stringify({
        id: instanceId,
        expires: Date.now() + LEASE_MS,
      }));
      leaseTimer = window.setTimeout(renewLease, Math.floor(LEASE_MS / 3));
    } catch (_error) {
      // Storage is optional; this tab continues with independent polling.
    }
  };

  const claimLeadership = () => {
    if (!channel) return true;
    const now = Date.now();
    const lease = readLease();
    if (lease && lease.id !== instanceId && Number(lease.expires || 0) > now) {
      stopLeaseHeartbeat();
      return false;
    }
    try {
      window.localStorage.setItem(leaseKey, JSON.stringify({ id: instanceId, expires: now + LEASE_MS }));
      const claimed = readLease()?.id === instanceId;
      if (claimed) renewLease();
      return claimed;
    } catch (_error) {
      return true;
    }
  };

  const accept = payload => {
    const nextCursor = payload?.cache?.cursor;
    if (nextCursor !== undefined && nextCursor !== null) cursor = String(nextCursor);
    notify(payload);
  };

  async function tick() {
    if (stopped || inFlight) return;
    if (!claimLeadership()) {
      schedule(FOLLOWER_CHECK_MS);
      return;
    }
    inFlight = true;
    let delay = IDLE_POLL_MS;
    try {
      const response = await getClientUpdates(cursor, 'cache,auth');
      if (stopped) return;
      accept(response);
      const hasEvents = (response?.cache?.events || []).length > 0;
      const hasChallenge = Boolean(response?.pending_auth?.required);
      delay = hasChallenge ? PENDING_AUTH_POLL_MS : (hasEvents ? EVENT_POLL_MS : IDLE_POLL_MS);
      channel?.postMessage({ type: 'snapshot', payload: response });
    } catch (_error) {
      // Polling is advisory; mounted pages retain their current snapshots.
    } finally {
      inFlight = false;
      if (document.visibilityState === 'hidden' && delay !== PENDING_AUTH_POLL_MS) {
        delay = Math.max(delay, IDLE_POLL_MS);
      }
      schedule(delay);
    }
  }

  if (typeof window.BroadcastChannel === 'function') {
    try {
      channel = new window.BroadcastChannel(`neu-client-updates:${namespace}`);
      channel.onmessage = event => {
        if (event.data?.type === 'snapshot') accept(event.data.payload || {});
      };
    } catch (_error) { channel = null; }
  }

  const wake = () => schedule(0);
  const visibility = () => {
    if (document.visibilityState === 'visible') wake();
  };
  window.addEventListener('neu-client-updates-wake', wake);
  document.addEventListener('visibilitychange', visibility);
  schedule(0);

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) this.stop();
      };
    },
    stop() {
      if (stopped) return;
      stopped = true;
      window.clearTimeout(timer);
      stopLeaseHeartbeat();
      window.removeEventListener('neu-client-updates-wake', wake);
      document.removeEventListener('visibilitychange', visibility);
      channel?.close();
      const lease = readLease();
      if (lease?.id === instanceId) {
        try { window.localStorage.removeItem(leaseKey); } catch (_error) { /* optional */ }
      }
      schedulers.delete(identity);
    },
  };
};

export const subscribeClientUpdates = (identity, listener) => {
  const key = String(identity || '');
  if (!key) return () => {};
  if (!schedulers.has(key)) schedulers.set(key, createScheduler(key));
  return schedulers.get(key).subscribe(listener);
};
