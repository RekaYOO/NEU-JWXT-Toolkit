import {
  localCodeRuntime,
  scheduleRoutePreloads,
  shouldPreloadAllRoutes,
} from './routePreload';

const fakeDocument = hidden => {
  const target = new EventTarget();
  Object.defineProperty(target, 'hidden', { configurable: true, writable: true, value: hidden });
  return target;
};

describe('route preload scheduling', () => {
  test('keeps local runtimes eligible and respects metered remote connections', () => {
    expect(localCodeRuntime({ runtimeProfile: 'desktop', locationRef: { hostname: 'example.test' } })).toBe(true);
    expect(shouldPreloadAllRoutes({ runtimeProfile: 'server', locationRef: { hostname: 'example.test' }, connection: { saveData: true } })).toBe(false);
    expect(shouldPreloadAllRoutes({ runtimeProfile: 'server', locationRef: { hostname: 'example.test' }, connection: { effectiveType: '2g' } })).toBe(false);
    expect(shouldPreloadAllRoutes({ runtimeProfile: 'server', locationRef: { hostname: 'example.test' }, connection: { effectiveType: '4g' } })).toBe(true);
  });

  test('preloads visible routes serially and resumes after visibility returns', async () => {
    const callbacks = [];
    const documentRef = fakeDocument(false);
    const windowRef = {
      location: { hostname: 'localhost' },
      requestIdleCallback: jest.fn(callback => { callbacks.push(callback); return callbacks.length; }),
      cancelIdleCallback: jest.fn(),
      setTimeout: jest.fn(),
      clearTimeout: jest.fn(),
    };
    const preload = jest.fn().mockResolvedValue(null);
    const cleanup = scheduleRoutePreloads({
      paths: ['/academic-report', '/grade-tracking', '/evaluation'],
      runtimeProfile: 'desktop', documentRef, windowRef, preload,
    });
    expect(callbacks).toHaveLength(1);
    await callbacks.shift()();
    expect(preload).toHaveBeenCalledTimes(1);
    expect(callbacks).toHaveLength(1);
    documentRef.hidden = true;
    documentRef.dispatchEvent(new Event('visibilitychange'));
    expect(windowRef.cancelIdleCallback).toHaveBeenCalled();
    documentRef.hidden = false;
    documentRef.dispatchEvent(new Event('visibilitychange'));
    expect(callbacks.length).toBeGreaterThanOrEqual(1);
    cleanup();
  });

  test('keeps background preload failures silent and continues with later routes', async () => {
    const callbacks = [];
    const documentRef = fakeDocument(false);
    const windowRef = {
      location: { hostname: 'localhost' },
      requestIdleCallback: jest.fn(callback => { callbacks.push(callback); return callbacks.length; }),
      cancelIdleCallback: jest.fn(),
      setTimeout: jest.fn(),
      clearTimeout: jest.fn(),
    };
    const preload = jest.fn()
      .mockRejectedValueOnce(new Error('chunk unavailable'))
      .mockResolvedValueOnce(null);
    const cleanup = scheduleRoutePreloads({
      paths: ['/academic-report', '/grade-tracking'],
      runtimeProfile: 'desktop', documentRef, windowRef, preload,
    });
    await callbacks.shift()();
    await Promise.resolve();
    expect(preload).toHaveBeenCalledTimes(1);
    await callbacks.shift()();
    expect(preload).toHaveBeenCalledTimes(2);
    cleanup();
  });
});
