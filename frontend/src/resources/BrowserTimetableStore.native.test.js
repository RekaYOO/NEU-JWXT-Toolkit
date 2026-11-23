const loadStore = serverUrl => {
  jest.resetModules();
  if (serverUrl) {
    window.NeuNative = { getShellInfo: () => JSON.stringify({ kind: 'client', server_url: serverUrl }) };
  } else {
    delete window.NeuNative;
  }
  return require('./BrowserTimetableStore');
};

afterEach(() => {
  delete window.NeuNative;
  delete window.indexedDB;
  localStorage.clear();
});

test('server switches partition timetable databases without deleting old caches', async () => {
  const open = jest.fn(() => { throw new Error('no database needed in this test'); });
  window.indexedDB = { open };
  await loadStore('https://first.test/').readBrowserTimetableCache('account');
  await loadStore('https://second.test/').readBrowserTimetableCache('account');
  await loadStore(null).readBrowserTimetableCache('account');

  expect(open.mock.calls.map(call => call[0])).toEqual([
    'neu-toolbox-browser-cache:https%3A%2F%2Ffirst.test%2F',
    'neu-toolbox-browser-cache:https%3A%2F%2Fsecond.test%2F',
    'neu-toolbox-browser-cache',
  ]);
});

test('offline recovery never reuses another server identity', () => {
  localStorage.setItem(
    'neu-toolbox-timetable-recovery-namespace:https%3A%2F%2Ffirst.test%2F', 'account:abcd',
  );
  expect(loadStore('https://first.test/').browserTimetableRecoveryIdentity()).toBe('__browser_namespace__:account:abcd');
  expect(loadStore('https://second.test/').browserTimetableRecoveryIdentity()).toBe('');
});

test('offline pointer follows committed writes and only its owner can clear it', async () => {
  let abort = false;
  const store = { put: jest.fn(), index: () => ({ openCursor: () => ({}) }) };
  window.indexedDB = { open: () => {
    const request = { result: { close: jest.fn(), transaction: () => {
      const tx = { objectStore: () => store };
      Promise.resolve().then(() => abort ? tx.onabort() : tx.oncomplete());
      return tx;
    } } };
    Promise.resolve().then(() => request.onsuccess());
    return request;
  } };
  const cache = loadStore(null);
  expect(await cache.writeBrowserTimetableCache('second')).toBe(true);
  const second = cache.browserTimetableRecoveryIdentity();
  expect(second).toBe(`__browser_namespace__:${cache.browserTimetableCacheNamespace('second')}`);
  abort = true;
  expect(await cache.writeBrowserTimetableCache('first')).toBe(false);
  expect(await cache.clearBrowserTimetableCache('second')).toBe(false);
  expect(cache.browserTimetableRecoveryIdentity()).toBe(second);
  abort = false;
  expect(await cache.clearBrowserTimetableCache('first')).toBe(true);
  expect(cache.browserTimetableRecoveryIdentity()).toBe(second);
  expect(await cache.clearBrowserTimetableCache('second')).toBe(true);
  expect(cache.browserTimetableRecoveryIdentity()).toBe('');
});
