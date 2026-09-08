import {
  nativeAxiosAdapter,
  nativeShellInfo,
  saveNativeFile,
} from './nativeBridge';

describe('native Android bridge', () => {
  afterEach(() => {
    delete window.NeuNative;
  });

  test('maps a native JSON response to an Axios response', async () => {
    let captured;
    window.NeuNative = {
      getShellInfo: () => JSON.stringify({ kind: 'client', server_url: 'https://example.test/' }),
      request: payload => {
        captured = JSON.parse(payload);
        return 'request-1';
      },
    };

    const pending = nativeAxiosAdapter({
      method: 'post',
      url: '/api/example',
      headers: { 'Content-Type': 'application/json' },
      data: { value: 3 },
      timeout: 2000,
    });
    window.__neuNativeDeliver('request-1', {
      status: 200,
      headers: { 'content-type': 'application/json' },
      body: '{"ok":true}',
    });

    await expect(pending).resolves.toMatchObject({ status: 200, data: { ok: true } });
    expect(captured).toMatchObject({
      method: 'POST', path: '/api/example', body: '{"value":3}', timeout_ms: 2000,
    });
    expect(nativeShellInfo()).toEqual({ kind: 'client', server_url: 'https://example.test/' });
  });

  test('rejects absolute and non-API URLs before entering native code', async () => {
    const request = jest.fn();
    window.NeuNative = { request };

    await expect(nativeAxiosAdapter({ url: 'https://evil.test/api/status' }))
      .rejects.toThrow('仅允许访问相对 /api/ 路径');
    await expect(nativeAxiosAdapter({ url: '/static/main.js' }))
      .rejects.toThrow('仅允许访问相对 /api/ 路径');
    expect(request).not.toHaveBeenCalled();
  });

  test('forwards AbortSignal cancellation to Android', async () => {
    const cancel = jest.fn();
    window.NeuNative = { request: () => 'request-2', cancel };
    const controller = new AbortController();
    const pending = nativeAxiosAdapter({ url: '/api/status', signal: controller.signal });

    controller.abort();

    await expect(pending).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(cancel).toHaveBeenCalledWith('request-2');
  });

  test.each(['/api/../health', '/api/%2e./health', '/api/%252e%252e/health',
    '/api/a%2fb', '/api/a\\b', '/api/a#fragment', '/api/a%00b'])('blocks unsafe path %s', async url => {
    const request = jest.fn();
    window.NeuNative = { request };
    await expect(nativeAxiosAdapter({ url })).rejects.toMatchObject({ code: 'ERR_INVALID_URL' });
    expect(request).not.toHaveBeenCalled();
  });

  test('supports synchronous native delivery and validateStatus', async () => {
    window.NeuNative = {
      request: () => {
        window.__neuNativeDeliver('sync', { status: 404, body: '{"detail":"missing"}' });
        return 'sync';
      },
    };
    await expect(nativeAxiosAdapter({ url: '/api/a', validateStatus: status => status === 404 }))
      .resolves.toMatchObject({ status: 404, data: { detail: 'missing' } });
  });

  test('preserves timeout errors without fabricating an HTTP response', async () => {
    window.NeuNative = { request: () => 'timeout' };
    const result = nativeAxiosAdapter({ url: '/api/status' });
    window.__neuNativeDeliver('timeout', { status: 0, code: 'ECONNABORTED', error: '请求超时' });
    await expect(result).rejects.toMatchObject({ isAxiosError: true, code: 'ECONNABORTED' });
  });

  test('already aborted signals never enter native code', async () => {
    const request = jest.fn();
    window.NeuNative = { request };
    const controller = new AbortController();
    controller.abort();
    await expect(nativeAxiosAdapter({ url: '/api/status', signal: controller.signal }))
      .rejects.toMatchObject({ __CANCEL__: true });
    expect(request).not.toHaveBeenCalled();
  });

  test('keeps native downloads out of JavaScript memory', async () => {
    const saveFile = jest.fn();
    window.NeuNative = { request: () => 'request-3', saveFile };
    const pending = nativeAxiosAdapter({ url: '/api/export/archive', responseType: 'blob' });
    window.__neuNativeDeliver('request-3', {
      status: 200,
      headers: { 'content-type': 'application/zip' },
      native_file_token: 'native-token',
      body_size: 5000000,
    });

    const response = await pending;

    expect(response.data).toBeInstanceOf(Blob);
    expect(response.data.size).toBe(5000000);
    expect(saveNativeFile('archive.zip', 'application/zip', response.data)).toBe(true);
    expect(saveFile).toHaveBeenCalledWith('archive.zip', 'application/zip', '@native:native-token');
  });

  test('streams generated HTML in bounded chunks before opening the file picker', async () => {
    const saveFile = jest.fn((_name, _type, payload) => payload === '@begin' ? 'upload-token' : 'ok');
    window.NeuNative = { saveFile };
    const blob = new Blob(['abc'], { type: 'text/html' });
    blob.slice = jest.fn(() => ({ arrayBuffer: async () => new Uint8Array([97, 98, 99]).buffer }));

    expect(saveNativeFile('outline.html', 'text/html', blob)).toBe(true);
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(blob.slice).toHaveBeenCalledWith(0, 65536);
    expect(saveFile.mock.calls).toEqual([
      ['outline.html', 'text/html', '@begin'],
      ['upload-token', '', '@chunk:YWJj'],
      ['outline.html', 'text/html', '@native:upload-token'],
    ]);
  });
});
