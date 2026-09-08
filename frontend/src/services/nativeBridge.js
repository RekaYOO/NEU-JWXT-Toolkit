import { AxiosError, CanceledError } from 'axios';

const pending = new Map();

const bridge = () => (typeof window !== 'undefined' ? window.NeuNative : null);

export const nativeShellInfo = () => {
  try {
    const value = bridge()?.getShellInfo?.();
    return value ? JSON.parse(value) : null;
  } catch (_error) {
    return null;
  }
};

export const isNativeShell = () => Boolean(bridge()?.request);

const isApiPath = value => {
  if (!value.startsWith('/api/') || /[\\#\x00-\x20]/.test(value)) return false;
  const path = value.split('?')[0].toLowerCase();
  if (/%(?:2f|5c|25)/.test(path)) return false;
  if (path.replaceAll('%2e', '.').split('/').some(part => part === '.' || part === '..')) return false;
  try {
    return !/[\x00-\x1f\x7f]/.test(decodeURIComponent(path));
  } catch (_error) {
    return false;
  }
};

const appendParams = (url, params, serializer) => {
  const serialize = typeof serializer === 'function' ? serializer : serializer?.serialize;
  if (serialize && params) {
    const suffix = serialize(params);
    return suffix ? `${url}${url.includes('?') ? '&' : '?'}${suffix}` : url;
  }
  if (!params || typeof params !== 'object') return url;
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    (Array.isArray(value) ? value : [value]).forEach(item => query.append(
      Array.isArray(value) ? `${key}[]` : key,
      item instanceof Date ? item.toISOString() : typeof item === 'object' ? JSON.stringify(item) : String(item),
    ));
  });
  const suffix = query.toString();
  return suffix ? `${url}${url.includes('?') ? '&' : '?'}${suffix}` : url;
};

const decodeBase64 = value => Uint8Array.from(
  atob(value || ''), character => character.charCodeAt(0),
);

const responseData = (payload, responseType) => {
  if (payload.native_file_token) {
    const placeholder = new Blob([], {
      type: payload.headers?.['content-type'] || 'application/octet-stream',
    });
    Object.defineProperty(placeholder, '__neuNativeFileToken', {
      value: payload.native_file_token,
    });
    Object.defineProperty(placeholder, 'size', {
      value: Math.max(0, Number(payload.body_size || 0)),
    });
    return placeholder;
  }
  if (payload.body_base64 !== undefined) {
    const bytes = decodeBase64(payload.body_base64);
    if (responseType === 'arraybuffer') return bytes.buffer;
    if (responseType === 'blob') {
      return new Blob([bytes], { type: payload.headers?.['content-type'] || 'application/octet-stream' });
    }
    return new TextDecoder().decode(bytes);
  }
  if (responseType === 'blob') {
    return new Blob([payload.body || ''], { type: payload.headers?.['content-type'] || 'application/octet-stream' });
  }
  if (responseType === 'text') return payload.body || '';
  if (payload.body === '' || payload.body === undefined || payload.body === null) return null;
  if (typeof payload.body !== 'string') return payload.body;
  try {
    return JSON.parse(payload.body);
  } catch (_error) {
    return payload.body;
  }
};

if (typeof window !== 'undefined') {
  window.__neuNativeDeliver = (requestId, rawPayload) => Promise.resolve().then(() => {
    const callbacks = pending.get(requestId);
    if (!callbacks) return;
    try {
      callbacks.resolve(typeof rawPayload === 'string' ? JSON.parse(rawPayload) : rawPayload);
    } catch (error) {
      callbacks.reject(error);
    }
  });
}

export const nativeAxiosAdapter = config => new Promise((resolve, reject) => {
  const native = bridge();
  const rawUrl = appendParams(String(config.url || ''), config.params, config.paramsSerializer);
  if (!native?.request || !isApiPath(rawUrl)) {
    reject(new AxiosError('原生网络桥仅允许访问相对 /api/ 路径', 'ERR_INVALID_URL', config));
    return;
  }
  if (config.signal?.aborted || config.cancelToken?.reason) {
    reject(new CanceledError(null, config));
    return;
  }
  if (config.data != null && typeof config.data !== 'string'
    && (config.data instanceof Blob || config.data instanceof FormData
      || config.data instanceof ArrayBuffer || ArrayBuffer.isView(config.data))) {
    reject(new AxiosError('原生网络桥不支持此请求体格式', 'ERR_NOT_SUPPORT', config));
    return;
  }
  const request = {
    method: String(config.method || 'get').toUpperCase(),
    path: rawUrl,
    headers: config.headers?.toJSON?.() || Object.fromEntries(
      Object.entries(config.headers || {}).filter(([, value]) => value != null && value !== false),
    ),
    body: typeof config.data === 'string'
      ? config.data
      : (config.data == null ? '' : JSON.stringify(config.data)),
    timeout_ms: Number(config.timeout || 30000),
    response_type: config.responseType || 'json',
    download: Boolean(config.nativeDownload),
  };
  let requestId;
  let timer;
  const cleanup = () => {
    pending.delete(requestId);
    clearTimeout(timer);
    config.signal?.removeEventListener('abort', abort);
    config.cancelToken?.unsubscribe?.(abort);
  };
  const fail = error => { cleanup(); reject(error); };
  const abort = () => {
    native.cancel?.(requestId);
    fail(new CanceledError(null, config));
  };
  try {
    requestId = native.request(JSON.stringify(request));
    if (!requestId) throw new Error('原生网络桥未接受请求');
  } catch (error) {
    reject(AxiosError.from(error, 'ERR_NETWORK', config));
    return;
  }
  pending.set(requestId, {
    resolve: payload => {
      if (!Number(payload.status || 0)) {
        fail(payload.code === 'ERR_CANCELED'
          ? new CanceledError(payload.error, config)
          : new AxiosError(payload.error || '网络请求失败', payload.code || 'ERR_NETWORK', config));
        return;
      }
      const response = {
        data: responseData(payload, config.responseType),
        status: Number(payload.status || 0),
        statusText: payload.status_text || '',
        headers: payload.headers || {},
        config,
        request: null,
      };
      const validate = config.validateStatus === undefined
        ? value => value >= 200 && value < 300 : config.validateStatus;
      cleanup();
      if (!validate || validate(response.status)) {
        resolve(response);
      } else {
        reject(new AxiosError(response.data?.detail || payload.error || `HTTP ${response.status}`,
          response.status >= 500 ? 'ERR_BAD_RESPONSE' : 'ERR_BAD_REQUEST', config, null, response));
      }
    },
    reject: error => fail(AxiosError.from(error, 'ERR_BAD_RESPONSE', config)),
  });
  config.signal?.addEventListener('abort', abort, { once: true });
  config.cancelToken?.subscribe?.(abort);
  timer = setTimeout(() => {
    native.cancel?.(requestId);
    fail(new AxiosError('请求超时', 'ECONNABORTED', config));
  }, Math.max(1000, Math.min(300000, request.timeout_ms)) + 1000);
  if (config.signal?.aborted) abort();
});

export const openNativeServerSettings = () => bridge()?.openServerSettings?.();

export const saveNativeFile = (filename, mediaType, blob) => {
  if (!bridge()?.saveFile || !(blob instanceof Blob)) return false;
  if (blob.__neuNativeFileToken) {
    bridge().saveFile(filename, mediaType || blob.type || 'application/octet-stream', `@native:${blob.__neuNativeFileToken}`);
    return true;
  }
  const native = bridge();
  const type = mediaType || blob.type || 'application/octet-stream';
  const token = native.saveFile(filename, type, '@begin');
  if (!token) return true;
  (async () => {
    try {
      for (let offset = 0; offset < blob.size; offset += 65536) {
        const bytes = new Uint8Array(await blob.slice(offset, offset + 65536).arrayBuffer());
        let binary = '';
        bytes.forEach(value => { binary += String.fromCharCode(value); });
        if (native.saveFile(token, '', `@chunk:${btoa(binary)}`) !== 'ok') {
          throw new Error('文件准备失败');
        }
      }
      native.saveFile(filename, type, `@native:${token}`);
    } catch (_error) {
      native.saveFile('', '', `@abort:${token}`);
    }
  })();
  return true;
};
