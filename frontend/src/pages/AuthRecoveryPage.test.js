import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import AuthRecoveryPage from './AuthRecoveryPage';
import {
  getAuthRecoveryStatus,
  pollAuthRecovery,
  sendAuthRecoverySMS,
  startAuthRecovery,
  verifyAuthRecoverySMS,
} from '../services/api';

jest.mock('../services/api', () => ({
  cancelAuthRecovery: jest.fn().mockResolvedValue({ success: true }),
  getAuthRecoveryStatus: jest.fn(),
  pollAuthRecovery: jest.fn(),
  refreshAuthRecoveryCaptcha: jest.fn(),
  sendAuthRecoverySMS: jest.fn(),
  startAuthRecovery: jest.fn(),
  verifyAuthRecoverySMS: jest.fn(),
}));

describe('AuthRecoveryPage', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test('expired link shows the server expiry message without restarting login', async () => {
    getAuthRecoveryStatus.mockRejectedValue({
      response: { status: 404, data: { detail: '一次性登录链接已过期，请进入工具箱重新登录' } },
    });
    const container = document.createElement('div');
    const root = createRoot(container);
    await act(async () => root.render(<AuthRecoveryPage token="expired-token" />));
    expect(container.textContent).toContain('链接已失效');
    expect(container.textContent).toContain('请进入工具箱重新登录');
    expect(startAuthRecovery).not.toHaveBeenCalled();
    expect(container.querySelector('button')).toBeNull();
    await act(async () => root.unmount());
  });

  test('expiry during QR polling keeps the expiry reason and stops polling', async () => {
    jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    let tick;
    jest.spyOn(window, 'setInterval').mockImplementation((callback, delay) => {
      if (delay === 3000) tick = callback;
      return delay;
    });
    const clear = jest.spyOn(window, 'clearInterval').mockImplementation(() => {});
    getAuthRecoveryStatus.mockResolvedValue({
      status: 'qr_pending', qr_content: 'test-qr', expires_in: 300, poll_interval: 3,
    });
    pollAuthRecovery.mockRejectedValue({
      response: { status: 404, data: { detail: '一次性登录链接已过期，请进入工具箱重新登录' } },
    });
    const container = document.createElement('div');
    const root = createRoot(container);
    await act(async () => root.render(<AuthRecoveryPage token="expired-token" />));
    await act(async () => tick());
    expect(container.textContent).toContain('请进入工具箱重新登录');
    expect(clear).toHaveBeenCalledWith(3000);
    expect(startAuthRecovery).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  test('后台账密已进入短信挑战时直接显示验证码，不重复创建二维码', async () => {
    getAuthRecoveryStatus.mockResolvedValue({
      status: 'sms_required',
      flow_id: 'sms-flow',
      captcha_image: 'data:image/jpeg;base64,YWJj',
      expires_in: 180,
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<AuthRecoveryPage token="recovery-token" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getAuthRecoveryStatus).toHaveBeenCalledWith('recovery-token');
    expect(startAuthRecovery).not.toHaveBeenCalled();
    expect(container.textContent).toContain('恢复教务登录');
    expect(container.textContent).toContain('完成短信二次认证');
    expect(container.textContent).toContain('图形验证码');
    expect(container.textContent).toContain('获取验证码');
    expect(container.querySelector('img[alt="图形验证码"]')).not.toBeNull();

    await act(async () => root.unmount());
    container.remove();
  });

  test('开始恢复时若直接续接短信挑战不会短暂显示空二维码', async () => {
    getAuthRecoveryStatus.mockResolvedValue({ status: 'ready' });
    startAuthRecovery.mockResolvedValue({
      status: 'sms_required',
      flow_id: 'sms-flow',
      captcha_image: 'data:image/jpeg;base64,YWJj',
      expires_in: 180,
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<AuthRecoveryPage token="recovery-token" />);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(startAuthRecovery).toHaveBeenCalledWith('recovery-token');
    expect(container.textContent).toContain('完成短信二次认证');
    expect(container.querySelector('.ant-qrcode')).toBeNull();
    expect(container.querySelector('img[alt="图形验证码"]')).not.toBeNull();

    await act(async () => root.unmount());
    container.remove();
  });

  test('短信预计时间归零后仍保留输入和重新发送能力', async () => {
    let countdownTick;
    jest.spyOn(window, 'setInterval').mockImplementation((callback, delay) => {
      if (delay === 1000) countdownTick = callback;
      return 1;
    });
    jest.spyOn(window, 'clearInterval').mockImplementation(() => {});
    getAuthRecoveryStatus.mockResolvedValue({
      status: 'sms_required',
      flow_id: 'sms-flow',
      captcha_image: 'data:image/jpeg;base64,YWJj',
      expires_in: 1,
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<AuthRecoveryPage token="recovery-token" />);
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      countdownTick();
    });

    expect(container.textContent).toContain('可重新发送或继续尝试验证');
    expect(container.textContent).toContain('获取验证码');
    expect(container.querySelector('input[placeholder="请输入短信验证码"]')).not.toBeNull();

    await act(async () => root.unmount());
    container.remove();
  });

  test('短信重发成功后重新开始五分钟预计时间', async () => {
    getAuthRecoveryStatus.mockResolvedValue({
      status: 'sms_required',
      flow_id: 'sms-flow',
      captcha_image: 'data:image/jpeg;base64,YWJj',
      expires_in: 120,
    });
    sendAuthRecoverySMS.mockResolvedValue({
      success: true,
      status: 'sent',
      expires_in: 300,
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<AuthRecoveryPage token="recovery-token" />);
      await Promise.resolve();
      await Promise.resolve();
    });
    const captchaInput = container.querySelector(
      'input[placeholder="请输入图片中的图形验证码"]'
    );
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
      ).set;
      setter.call(captchaInput, '1234');
      captchaInput.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const sendButton = Array.from(container.querySelectorAll('button')).find(
      button => button.textContent.includes('获取验证码')
    );
    await act(async () => {
      sendButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(sendAuthRecoverySMS).toHaveBeenCalledWith(
      'recovery-token', '1234'
    );
    expect(container.textContent).toContain('05:00');
    expect(container.textContent).toContain('重新发送');

    await act(async () => root.unmount());
    container.remove();
  });

  test.each(['success', 'flow_missing', 'send_failed', 'captcha_invalid'])(
    '短信操作显示实际结果而不是停留或误报：%s', async (scenario) => {
      getAuthRecoveryStatus.mockResolvedValue({
        status: 'sms_required', flow_id: 'sms',
        captcha_image: 'data:image/jpeg;base64,YWJj', expires_in: 300,
      });
      verifyAuthRecoverySMS.mockResolvedValue({ status: 'authenticated' });
      sendAuthRecoverySMS.mockResolvedValue({
        success: false, status: 'error', message: '发送频率受限，请稍后重试',
      });
      if (scenario === 'flow_missing') verifyAuthRecoverySMS.mockRejectedValue({
        response: { status: 409, data: { detail: {
          error_code: 'WEBVPN_FLOW_MISSING',
          message: '短信验证流程已失效，请在本页重新开始登录',
        } } },
      });
      if (scenario === 'captcha_invalid') sendAuthRecoverySMS.mockResolvedValue({
        success: false, status: 'captcha_invalid', captcha_invalid: true,
        captcha_image: 'data:image/jpeg;base64,bmV3', message: '图形验证码不正确',
      });
      const container = document.createElement('div');
      document.body.appendChild(container);
      const root = createRoot(container);
      try {
        await act(async () => root.render(<AuthRecoveryPage token="recovery-token" />));
        const isSend = ['send_failed', 'captcha_invalid'].includes(scenario);
        const input = container.querySelector(isSend
          ? 'input[placeholder="请输入图片中的图形验证码"]'
          : 'input[placeholder="请输入短信验证码"]');
        await act(async () => {
          Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
            .set.call(input, '123456');
          input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const button = [...container.querySelectorAll('button')].find(element => (
          element.textContent.includes(isSend ? '获取验证码' : '验证并恢复登录')
        ));
        await act(async () => button.click());
        if (scenario === 'success') {
          expect(container.textContent).toContain('登录已恢复');
          expect(container.querySelector('input[placeholder="请输入短信验证码"]')).toBeNull();
          expect(verifyAuthRecoverySMS).toHaveBeenCalledTimes(1);
        } else if (scenario === 'flow_missing') {
          expect(container.textContent).toContain('请在本页重新开始登录');
          expect(container.textContent).toContain('重新开始登录');
          expect(container.textContent).not.toContain('链接已失效');
        } else if (scenario === 'captcha_invalid') {
          expect(container.textContent).toContain('图形验证码不正确');
          expect(input.value).toBe('');
          expect(container.querySelector('img[alt="图形验证码"]').src).toBe('data:image/jpeg;base64,bmV3');
          expect(container.querySelector('input[placeholder="请输入短信验证码"]')).not.toBeNull();
          expect(button.disabled).toBe(true);
          expect(sendAuthRecoverySMS).toHaveBeenCalledTimes(1);
        } else {
          expect(container.textContent).toContain('发送频率受限');
          expect(container.textContent).not.toContain('短信验证码已发送');
        }
      } finally {
        await act(async () => root.unmount());
        container.remove();
      }
    },
  );
});
