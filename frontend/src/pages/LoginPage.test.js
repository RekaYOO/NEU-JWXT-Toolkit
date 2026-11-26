import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import LoginPage from './LoginPage';
import {
  checkStatus,
  getOfflineStatus,
  startWebVPNQRLogin,
  cancelWebVPNQRLogin,
  startWebVPNPasswordLogin,
  login,
} from '../services/api';
import {
  MANUAL_LOGOUT_SESSION_KEY,
} from '../utils/authSessionPolicy';

jest.mock('../services/api', () => ({
  login: jest.fn(),
  checkStatus: jest.fn(),
  startWebVPNQRLogin: jest.fn(),
  getWebVPNQRStatus: jest.fn(),
  cancelWebVPNQRLogin: jest.fn().mockResolvedValue({ success: true }),
  startWebVPNPasswordLogin: jest.fn(),
  sendWebVPNSMSCode: jest.fn(),
  verifyWebVPNSMSCode: jest.fn(),
  cancelWebVPNSMSLogin: jest.fn().mockResolvedValue({ success: true }),
  getOfflineStatus: jest.fn(),
  isWebVPNCampusNetworkBlocked: jest.fn(value => value?.error_code === 'WEBVPN_CAMPUS_NETWORK_BLOCKED'),
  getWebVPNErrorMessage: jest.fn((value, fallback = 'WebVPN 操作失败') => value?.message || fallback),
  isWebVPNFlowInvalid: jest.fn(() => false),
}));

describe('LoginPage session recovery boundary', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    sessionStorage.clear();
    checkStatus.mockReset();
    checkStatus.mockResolvedValue({ is_logged_in: false, current_user: null });
    startWebVPNQRLogin.mockReset();
    startWebVPNPasswordLogin.mockReset();
    login.mockReset();
    cancelWebVPNQRLogin.mockReset();
    cancelWebVPNQRLogin.mockResolvedValue({ success: true });
    getOfflineStatus.mockReset();
    getOfflineStatus.mockResolvedValue({
      available: false,
      has_scores: false,
      has_report: false,
      has_research: false,
      resources: [],
    });
    window.matchMedia = jest.fn().mockImplementation((query) => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }));
  });

  const renderLoginPage = async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const onLoginSuccess = jest.fn();

    await act(async () => {
      root.render(
        <LoginPage
          onLoginSuccess={onLoginSuccess}
          onOfflineSuccess={jest.fn()}
        />
      );
      await Promise.resolve();
    });

    return { container, root, onLoginSuccess };
  };

  const submitPassword = async (container, fillCredentials = true) => {
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    await act(async () => {
      if (fillCredentials) {
        for (const [placeholder, value] of [['学号', 'fixture-student'], ['密码', 'fixture-password']]) {
          const input = container.querySelector(`input[placeholder="${placeholder}"]`);
          setValue.call(input, value);
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
      container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
  };

  test('主动退出后挂载登录页不触发自动会话恢复', async () => {
    sessionStorage.setItem(MANUAL_LOGOUT_SESSION_KEY, '1');
    const { container, root, onLoginSuccess } = await renderLoginPage();

    expect(getOfflineStatus).toHaveBeenCalledTimes(1);
    expect(checkStatus).not.toHaveBeenCalled();
    expect(onLoginSuccess).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    container.remove();
  });

  test('自然会话失效进入登录页时仍尝试自动恢复', async () => {
    checkStatus.mockResolvedValue({
      is_logged_in: true,
      current_user: '20240001',
    });
    const { container, root, onLoginSuccess } = await renderLoginPage();

    expect(checkStatus).toHaveBeenCalledTimes(1);
    expect(onLoginSuccess).toHaveBeenCalledWith('20240001');

    await act(async () => root.unmount());
    container.remove();
  });

  test('NEU 账号表单使用独立自动填充分区', async () => {
    const { container, root } = await renderLoginPage();
    const username = container.querySelector('input[placeholder="学号"]');
    const password = container.querySelector('input[placeholder="密码"]');

    expect(username?.getAttribute('autocomplete')).toBe('section-neu-account username');
    expect(password?.getAttribute('autocomplete')).toBe('section-neu-account current-password');
    expect(username?.name).toBe('username');
    expect(password?.name).toBe('password');

    await act(async () => root.unmount());
    container.remove();
  });

  test('扫码跟随所选线路，切换提供方或线路会取消旧二维码', async () => {
    startWebVPNQRLogin.mockResolvedValue({
      success: true,
      flow_id: 'qr-flow',
      qr_content: 'fixture-qr',
      poll_interval: 60,
      expires_in: 120,
    });
    const { container, root } = await renderLoginPage();
    const [smart, wechat] = container.querySelectorAll('.qr-method-card');

    await act(async () => smart.click());
    expect(smart.getAttribute('aria-expanded')).toBe('true');
    expect(startWebVPNQRLogin).toHaveBeenLastCalledWith('', 'primary', 'smart_neu', 'direct');
    expect(container.querySelector('form').closest('.login-fold').getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('input[value="direct"]').checked).toBe(true);

    await act(async () => wechat.click());
    expect(cancelWebVPNQRLogin).toHaveBeenCalledWith('qr-flow');
    expect(startWebVPNQRLogin).toHaveBeenLastCalledWith('', 'primary', 'wechat', 'direct');
    expect(wechat.getAttribute('aria-expanded')).toBe('true');

    await act(async () => container.querySelector('input[value="webvpn"]').click());
    expect(cancelWebVPNQRLogin).toHaveBeenCalledTimes(2);
    expect(container.querySelector('.qr-panel')).toBeNull();
    expect(container.querySelector('form').closest('.login-fold').getAttribute('aria-hidden')).toBe('false');
    await act(async () => wechat.click());
    expect(startWebVPNQRLogin).toHaveBeenLastCalledWith('', 'primary', 'wechat', 'webvpn');
    await act(async () => root.unmount());
    container.remove();
  });

  test.each(['DIRECT_ACCESS_FAILED', 'WEBVPN_REQUIRED', 'UPSTREAM_UNAVAILABLE', 'WRONG_PASSWORD', 'WEBVPN_REQUIRED_AFTER_SUBMIT'])(
    '密码登录按 %s 判断是否换线并接续原短信挑战',
    async errorCode => {
      login.mockResolvedValue({ success: false, error_code: errorCode, message: '模拟错误' });
      startWebVPNPasswordLogin.mockResolvedValue({
        success: true, status: 'sms_required', flow_id: 'sms-flow', expires_in: 300,
      });
      const { container, root } = await renderLoginPage();
      await submitPassword(container);

      expect(login).toHaveBeenCalledTimes(1);
      expect(login).toHaveBeenCalledWith('fixture-student', 'fixture-password', true, 'direct', expect.any(AbortSignal));
      if (['DIRECT_ACCESS_FAILED', 'WEBVPN_REQUIRED'].includes(errorCode)) {
        expect(startWebVPNPasswordLogin).toHaveBeenCalledTimes(1);
        expect(startWebVPNPasswordLogin).toHaveBeenCalledWith('fixture-student', 'fixture-password', true, 'primary', login.mock.calls[0][4]);
        expect(container.querySelector('input[value="webvpn"]').checked).toBe(true);
        expect(document.querySelector('.ant-modal-title')?.textContent).toBe('短信二次认证');
      } else {
        expect(startWebVPNPasswordLogin).not.toHaveBeenCalled();
        expect(container.querySelector('input[value="direct"]').checked).toBe(true);
      }
      await act(async () => root.unmount());
      container.remove();
    },
  );

  test.each([
    { success: false, error_code: 'DIRECT_ACCESS_FAILED' },
    { success: true, username: 'old-attempt' },
  ])('取消直连后忽略晚到的响应 %#，并可用原表单重试', async lateResponse => {
    let finishOldRequest;
    let finishNewRequest;
    login.mockImplementationOnce(() => new Promise(resolve => { finishOldRequest = resolve; }));
    login.mockImplementationOnce(() => new Promise(resolve => { finishNewRequest = resolve; }));
    const { container, root, onLoginSuccess } = await renderLoginPage();
    const button = container.querySelector('.login-password-action');
    await submitPassword(container);
    const cancelledSignal = login.mock.calls[0][4];
    expect(button.textContent).toBe('取消登录');
    expect(button.type).toBe('button');
    expect(button.disabled).toBe(false);
    expect(container.querySelector('.login-connection-status').textContent).toContain('正在连接教务系统');

    await act(async () => button.click());
    expect(cancelledSignal.aborted).toBe(true);
    expect(button.type).toBe('submit');
    expect(container.querySelector('input[placeholder="密码"]').disabled).toBe(false);
    expect(container.querySelector('.login-connection-status').textContent).toBe('');
    await submitPassword(container, false);
    const retrySignal = login.mock.calls[1][4];
    expect(login).toHaveBeenLastCalledWith('fixture-student', 'fixture-password', true, 'direct', retrySignal);
    expect(retrySignal).not.toBe(cancelledSignal);
    expect(retrySignal.aborted).toBe(false);

    await act(async () => finishOldRequest(lateResponse));
    expect(startWebVPNPasswordLogin).not.toHaveBeenCalled();
    expect(onLoginSuccess).not.toHaveBeenCalled();
    expect(button.textContent).toBe('取消登录');
    await act(async () => finishNewRequest({ success: true, username: 'retried-attempt' }));
    expect(onLoginSuccess).toHaveBeenCalledTimes(1);
    expect(onLoginSuccess).toHaveBeenCalledWith('retried-attempt');
    await act(async () => root.unmount());
    container.remove();
  });

  test.each(['selected', 'fallback'])('取消 WebVPN 密码登录后忽略短信挑战（%s）', async mode => {
    let finishRequest;
    login.mockResolvedValue({ success: false, error_code: 'DIRECT_ACCESS_FAILED' });
    startWebVPNPasswordLogin.mockImplementation(() => new Promise(resolve => { finishRequest = resolve; }));
    const { container, root, onLoginSuccess } = await renderLoginPage();
    const webvpn = container.querySelector('input[value="webvpn"]');
    if (mode === 'selected') await act(async () => webvpn.click());
    await submitPassword(container);
    const signal = startWebVPNPasswordLogin.mock.calls[0][4];
    const button = container.querySelector('.login-password-action');
    expect(button.textContent).toBe('取消登录');
    expect(container.querySelector('.login-connection-status').textContent).toContain('WebVPN');

    await act(async () => button.click());
    await act(async () => finishRequest({ success: true, status: 'sms_required', flow_id: 'stale-flow' }));
    expect(signal.aborted).toBe(true);
    expect(webvpn.checked).toBe(true);
    expect(webvpn.disabled).toBe(false);
    expect(button.textContent.replace(/\s/g, '')).toBe('登录');
    expect(document.querySelector('.ant-modal-title')).toBeNull();
    expect(onLoginSuccess).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    container.remove();
  });

  test('退出登录页面时取消仍在等待的密码请求', async () => {
    login.mockImplementation(() => new Promise(() => {}));
    const { container, root } = await renderLoginPage();
    await submitPassword(container);
    const signal = login.mock.calls[0][4];
    await act(async () => root.unmount());
    expect(signal.aborted).toBe(true);
    container.remove();
  });
});
