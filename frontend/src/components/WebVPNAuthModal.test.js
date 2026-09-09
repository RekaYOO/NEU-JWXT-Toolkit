import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import WebVPNAuthModal from './WebVPNAuthModal';

describe('WebVPNAuthModal responsive authentication layout', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  test('uses accessible compact controls and the visible viewport height', async () => {
    const addEventListener = jest.fn();
    const removeEventListener = jest.fn();
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: {
        height: 640,
        addEventListener,
        removeEventListener,
      },
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <WebVPNAuthModal
          flow={{ captcha_image: 'data:image/jpeg;base64,YWJj' }}
          captchaCode=""
          setCaptchaCode={jest.fn()}
          smsCode=""
          setSmsCode={jest.fn()}
          onRefreshCaptcha={jest.fn()}
          onSendSMS={jest.fn()}
          onVerify={jest.fn()}
          onCancel={jest.fn()}
        />
      );
      await Promise.resolve();
    });

    expect(document.querySelector('.captcha-auth-grid')).not.toBeNull();
    expect(document.querySelector('input[aria-label="图形验证码"]')).not.toBeNull();
    expect(document.querySelector('input[aria-label="短信验证码"]')).not.toBeNull();
    expect(document.querySelector('button[aria-label="刷新图形验证码"]')).not.toBeNull();
    expect(document.querySelector('.login-sms-modal .ant-modal').style.getPropertyValue(
      '--auth-viewport-height'
    )).toBe('640px');
    expect(document.querySelector('.login-sms-modal .ant-btn-primary').disabled).toBe(true);
    expect(addEventListener).toHaveBeenCalledWith('resize', expect.any(Function));

    await act(async () => root.unmount());
    expect(removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function));
    container.remove();
  });

  test('verified SMS shows a session retry without requesting another code', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const verify = jest.fn();
    await act(async () => root.render(
      <WebVPNAuthModal flow={{ sms_verified: true }} smsCode="" onVerify={verify} />,
    ));
    expect(document.querySelector('input[aria-label="短信验证码"]')).toBeNull();
    expect(document.querySelector('button[aria-label="刷新图形验证码"]')).toBeNull();
    const button = document.querySelector('.login-sms-modal .ant-btn-primary');
    expect(button.textContent).toBe('继续建立会话');
    expect(button.disabled).toBe(false);
    await act(async () => button.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(verify).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
    container.remove();
  });
});
