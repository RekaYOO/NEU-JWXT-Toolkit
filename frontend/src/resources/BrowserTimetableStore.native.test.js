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
