import {
  currentMainAsset,
  isChunkLoadError,
  recoverFromStaleBuild,
} from './routeLoadRecovery';

const manifestResponse = main => ({
  ok: true,
  json: async () => ({ files: { 'main.js': main } }),
});

describe('route chunk recovery', () => {
  beforeEach(() => {
    document.head.innerHTML = '<script src="/static/js/main.old12345.js"></script>';
  });

  test('recognizes browser chunk loading failures', () => {
    expect(isChunkLoadError(Object.assign(new Error('Loading chunk 12 failed'), { name: 'ChunkLoadError' }))).toBe(true);
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module'))).toBe(true);
    expect(isChunkLoadError(new Error('page render failed'))).toBe(false);
    expect(currentMainAsset()).toBe('/static/js/main.old12345.js');
  });

  test('reloads once when the manifest belongs to a newer build', async () => {
    const storage = { getItem: jest.fn(() => null), setItem: jest.fn() };
    const reload = jest.fn();
    const result = await recoverFromStaleBuild({
      error: new Error('Loading chunk 12 failed'),
      fetchImpl: jest.fn().mockResolvedValue(manifestResponse('/static/js/main.new67890.js')),
      storage,
      reload,
    });
    expect(result).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith('neu-route-chunk-reload:/static/js/main.new67890.js', '1');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('does not reload the same build or repeat a guarded reload', async () => {
    const error = new Error('Loading chunk 12 failed');
    const reload = jest.fn();
    await expect(recoverFromStaleBuild({
      error,
      fetchImpl: jest.fn().mockResolvedValue(manifestResponse('/static/js/main.old12345.js')),
      storage: { getItem: () => null, setItem: jest.fn() }, reload,
    })).resolves.toBe(false);
    await expect(recoverFromStaleBuild({
      error,
      fetchImpl: jest.fn().mockResolvedValue(manifestResponse('/static/js/main.new67890.js')),
      storage: { getItem: () => '1', setItem: jest.fn() }, reload,
    })).resolves.toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
