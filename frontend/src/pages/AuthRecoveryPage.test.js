import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import AuthRecoveryPage from './AuthRecoveryPage';
import {
  getAuthRecoveryStatus,
  sendAuthRecoverySMS,
  startAuthRecovery,
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
});
