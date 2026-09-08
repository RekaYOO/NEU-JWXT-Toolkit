import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { message } from 'antd';
import LoginPage from './LoginPage';
import CourseOutlinePage from './CourseOutlinePage';

// Keep the real API interceptors and Axios adapter; only the Android boundary
// is simulated so the same page workflows run for both APKs.
jest.mock('../services/nativeBridge', () => ({
  ...jest.requireActual('../services/nativeBridge'),
  isNativeShell: () => true,
}));

describe.each(['local', 'client'])('%s native workflow parity', kind => {
  let container;
  let root;
  let requests;
  let handler;
  let sequence;

  const flush = async () => {
    for (let index = 0; index < 30; index += 1) await Promise.resolve();
  };
  const advance = async ms => {
    await act(async () => {
      jest.advanceTimersByTime(ms);
      await flush();
    });
  };
  const input = async (selector, value) => {
    await act(async () => {
      const element = document.querySelector(selector);
      expect(element).not.toBeNull();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, value);
      element.dispatchEvent(new Event('input', { bubbles: true }));
      await flush();
    });
  };
  const submit = async () => {
    await act(async () => {
      container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await flush();
    });
  };
  const click = async text => {
    await act(async () => {
      const button = Array.from(document.querySelectorAll('button'))
        .find(item => item.textContent.replace(/\s/g, '').includes(text));
      expect(button).toBeDefined();
      button.click();
      await flush();
    });
  };
  const render = async element => {
    await act(async () => {
      root.render(element);
      await flush();
    });
  };

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    localStorage.clear();
    sessionStorage.clear();
    window.matchMedia = jest.fn(() => ({
      matches: false, addListener: jest.fn(), removeListener: jest.fn(),
      addEventListener: jest.fn(), removeEventListener: jest.fn(),
    }));
    requests = [];
    sequence = 0;
    handler = path => {
      if (path === '/api/offline/status') return { available: false };
      if (path === '/api/status') return { is_logged_in: false };
      throw new Error(`Unexpected API request: ${path}`);
    };
    window.NeuNative = {
      getShellInfo: () => JSON.stringify({ kind }),
      request: raw => {
        const request = JSON.parse(raw);
        const id = `parity-${++sequence}`;
        requests.push(request);
        Promise.resolve().then(() => handler(request.path, request))
          .then(body => window.__neuNativeDeliver(id, {
            status: 200, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
          }));
        return id;
      },
      cancel: jest.fn(),
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
      message.destroy();
      await flush();
    });
    container.remove();
    delete window.NeuNative;
    jest.useRealTimers();
  });

  test('slow direct failure switches to WebVPN and a single SMS verification completes login', async () => {
    const initialHandler = handler;
    handler = path => {
      if (path === '/api/login') return new Promise(resolve => setTimeout(() => resolve({
        success: false, requires_webvpn: true, error_code: 'DIRECT_ACCESS_FAILED',
        suggestion: '请检查校园网络；校外网络请选择 WebVPN。',
      }), 11000));
      if (path === '/api/webvpn/password/start') return {
        success: true, status: 'sms_required', flow_id: 'synthetic-sms',
        captcha_image: 'data:image/png;base64,YWJj', expires_in: 300,
      };
      if (path === '/api/webvpn/sms/send') return { success: true, status: 'sent' };
      if (path === '/api/webvpn/sms/verify') return new Promise(resolve => setTimeout(() => resolve({
        success: true, status: 'authenticated', username: '20250001',
      }), 11000));
      return initialHandler(path);
    };
    const onLoginSuccess = jest.fn();
    await render(<LoginPage onLoginSuccess={onLoginSuccess} onOfflineSuccess={jest.fn()} />);
    await input('input[placeholder="学号"]', '20250001');
    await input('input[placeholder="密码"]', 'synthetic-password');
    await submit();
    await advance(11000);
    expect(container.querySelector('input[value="webvpn"]').checked).toBe(true);
    expect(requests.find(request => request.path === '/api/login').timeout_ms).toBe(30000);
    await submit();
    await input('input[aria-label="图形验证码"]', 'test');
    await click('获取验证码');
    await input('input[aria-label="短信验证码"]', '123456');
    await click('验证并登录');
    await advance(10000);
    expect(onLoginSuccess).not.toHaveBeenCalled();
    await advance(1000);
    expect(onLoginSuccess).toHaveBeenCalledTimes(1);
    expect(onLoginSuccess).toHaveBeenCalledWith('20250001');
    expect(requests.filter(request => request.path === '/api/webvpn/sms/verify')).toHaveLength(1);
    expect(window.NeuNative.cancel).not.toHaveBeenCalled();
  });

  test('entering outlines automatically loads the first page without storing full outlines', async () => {
    handler = path => {
      if (path === '/api/course-outlines/search-schema') return { fields: [] };
      if (path === '/api/course-outlines/search') return new Promise(resolve => setTimeout(() => resolve({
        items: [{ course_code: 'TEST001', course_name: 'Synthetic outline', credits: 2, hours: 32 }],
        total: 1, page: 1, page_size: 20,
      }), 11000));
      throw new Error(`Unexpected API request: ${path}`);
    };
    await render(<CourseOutlinePage />);
    await advance(300);
    expect(requests.filter(request => request.path === '/api/course-outlines/search')).toHaveLength(1);
    await advance(11000);
    expect(container.textContent).toContain('Synthetic outline');
    expect(JSON.parse(requests.find(request => request.path.endsWith('/search')).body)).toMatchObject({
      keyword: '', page: 1, page_size: 20,
    });
    expect(localStorage.length).toBe(0);
    expect(window.NeuNative.cancel).not.toHaveBeenCalled();
  });
});
