jest.mock('./api', () => ({
  getClientUpdates: jest.fn(),
}));

import { getClientUpdates } from './api';
import { subscribeClientUpdates } from './ClientUpdateScheduler';


describe('ClientUpdateScheduler', () => {
  const originalBroadcastChannel = window.BroadcastChannel;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    window.localStorage.clear();
    window.BroadcastChannel = class {
      close() {}
      postMessage() {}
    };
    getClientUpdates.mockResolvedValue({
      cache: { events: [], cursor: 1 },
      pending_auth: { required: false },
    });
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    window.BroadcastChannel = originalBroadcastChannel;
  });

  test('shares one poller between subscribers for the same account', async () => {
    const first = jest.fn();
    const second = jest.fn();
    const unsubscribeFirst = subscribeClientUpdates('student-a', first);
    const unsubscribeSecond = subscribeClientUpdates('student-a', second);

    jest.advanceTimersByTime(0);
    await getClientUpdates.mock.results[0].value;
    for (let index = 0; index < 4; index += 1) await Promise.resolve();

    expect(getClientUpdates).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    unsubscribeFirst();
    unsubscribeSecond();
  });

  test('renews the leader lease without adding an idle server poll', async () => {
    const unsubscribe = subscribeClientUpdates('student-lease', jest.fn());
    jest.advanceTimersByTime(0);
    await getClientUpdates.mock.results[0].value;
    for (let index = 0; index < 4; index += 1) await Promise.resolve();
    const leaseKey = Object.keys(window.localStorage)
      .find(key => key.startsWith('neu-client-updates-leader:'));
    const firstExpiry = JSON.parse(window.localStorage.getItem(leaseKey)).expires;

    jest.advanceTimersByTime(5000);

    const renewedExpiry = JSON.parse(window.localStorage.getItem(leaseKey)).expires;
    expect(renewedExpiry).toBeGreaterThan(firstExpiry);
    expect(getClientUpdates).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
