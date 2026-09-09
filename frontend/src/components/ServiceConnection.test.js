import React, { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { ServiceWebVPNLogin, ServiceAuthNotice, hasServiceAuthOwner } from './ServiceConnection';
import * as api from '../services/api';

jest.mock('antd', () => ({
  ...jest.requireActual('antd'),
  QRCode: () => <div data-testid="qr" />,
}));
jest.mock('../services/api', () => ({
  getPendingAuthChallenge: jest.fn(),
  startWebVPNPasswordLogin: jest.fn(), startWebVPNQRLogin: jest.fn(),
  getWebVPNQRStatus: jest.fn(), cancelWebVPNQRLogin: jest.fn(),
  refreshWebVPNCaptcha: jest.fn(), sendWebVPNSMSCode: jest.fn(),
  verifyWebVPNSMSCode: jest.fn(), cancelWebVPNSMSLogin: jest.fn(),
  getWebVPNErrorMessage: value => value.message || '认证失败',
  isWebVPNFlowInvalid: value => value.error_code === 'WEBVPN_FLOW_EXPIRED',
  isWebVPNCampusNetworkBlocked: value => value.error_code === 'WEBVPN_CAMPUS_NETWORK_BLOCKED',
}));
jest.mock('./WebVPNAuthModal', () => props => props.flow ? (
  <div data-testid="sms">
    <span>{props.flow.flow_id}</span><span>{props.flow.captcha_image}</span><span>{props.error}</span>
    <input aria-label="captcha" value={props.captchaCode} onChange={e => props.setCaptchaCode(e.target.value)} />
    <input aria-label="sms" value={props.smsCode} onChange={e => props.setSmsCode(e.target.value)} />
    <button onClick={props.onSendSMS}>发送短信</button>
    <button onClick={props.onVerify}>验证</button>
    <button onClick={props.onCancel}>取消短信</button>
  </div>
) : null);

const click = element => element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text);
const fill = (element, text) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, text);
  element.dispatchEvent(new Event('input', { bubbles: true }));
};
const settle = async action => act(async () => {
  await action?.();
  await Promise.resolve();
});

describe('shared service WebVPN recovery', () => {
  let root, container, login, done;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    api.getPendingAuthChallenge.mockResolvedValue({ required: false });
    api.cancelWebVPNQRLogin.mockResolvedValue({ success: true });
    api.cancelWebVPNSMSLogin.mockResolvedValue({ success: true });
    window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    login = createRef();
    done = jest.fn();
  });
  afterEach(async () => {
    await settle(() => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });
  const render = (service = 'cxcy', offline = false) => settle(() => root.render(
    <ServiceWebVPNLogin ref={login} service={service} offline={offline}
      username="20250001" onAuthenticated={done} />,
  ));

  test.each(['jwxk', 'cxcy'])('%s password and SMS continue the same service flow', async service => {
    api.startWebVPNPasswordLogin.mockResolvedValue({
      success: true, status: 'sms_required', flow_id: 'same-flow', captcha_image: 'old-image',
    });
    api.sendWebVPNSMSCode.mockResolvedValue({
      success: false, status: 'captcha_invalid', captcha_invalid: true,
      captcha_image: 'new-image', message: '图形验证码错误',
    });
    await render(service);
    expect(hasServiceAuthOwner(service)).toBe(true);
    await settle(() => login.current.open('password'));
    await settle(() => fill(document.querySelector('input[placeholder="密码"]'), 'fixture-only'));
    await settle(() => click(button('恢复 WebVPN 登录')));
    expect(api.startWebVPNPasswordLogin).toHaveBeenCalledWith('20250001', 'fixture-only', false, service);
    expect(api.sendWebVPNSMSCode).not.toHaveBeenCalled();
    await settle(() => fill(document.querySelector('[aria-label="captcha"]'), 'abcd'));
    await settle(() => click(button('发送短信')));
    expect(container.textContent).toContain('new-image');
    expect(document.querySelector('[aria-label="captcha"]').value).toBe('');
    expect(done).not.toHaveBeenCalled();
    api.verifyWebVPNSMSCode.mockResolvedValue({ success: true, status: 'authenticated', target_service: service });
    await settle(() => fill(document.querySelector('[aria-label="sms"]'), '123456'));
    await settle(() => click(button('验证')));
    expect(api.verifyWebVPNSMSCode).toHaveBeenCalledWith('same-flow', '123456');
    expect(done).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="sms"]')).toBeNull();
  });

  test('continues a pending CXCY challenge without creating a new login', async () => {
    api.getPendingAuthChallenge.mockResolvedValue({
      required: true, target_service: 'cxcy', flow_id: 'pending-flow', captcha_image: 'image',
    });
    await render();
    await settle(() => login.current.open('password'));
    expect(container.textContent).toContain('pending-flow');
    expect(api.startWebVPNPasswordLogin).not.toHaveBeenCalled();
    expect(api.startWebVPNQRLogin).not.toHaveBeenCalled();
    await settle(() => click(button('取消短信')));
    expect(api.cancelWebVPNSMSLogin).toHaveBeenCalledWith('pending-flow');
  });

  test('cancels a late QR creation instead of reopening a closed modal', async () => {
    let finish;
    api.startWebVPNQRLogin.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await render();
    let opening;
    await settle(() => { opening = login.current.open('qr'); });
    await settle(() => login.current.close());
    await settle(() => finish({ success: true, flow_id: 'late', qr_content: 'fixture' }));
    await opening;
    expect(api.cancelWebVPNQRLogin).toHaveBeenCalledWith('late');
    expect(api.getWebVPNQRStatus).not.toHaveBeenCalled();
  });

  test('QR completion resumes activity reads once', async () => {
    api.startWebVPNQRLogin.mockResolvedValue({ success: true, flow_id: 'qr-flow', qr_content: 'fixture' });
    api.getWebVPNQRStatus.mockResolvedValue({ success: true, status: 'authenticated', target_service: 'cxcy' });
    await render();
    await settle(() => login.current.open('qr'));
    expect(api.startWebVPNQRLogin).toHaveBeenCalledWith('20250001', 'cxcy');
    expect(done).toHaveBeenCalledTimes(1);
  });

  test('a completed gateway flow preserves business service unavailability', async () => {
    const result = {
      success: true, status: 'authenticated', target_service: 'cxcy',
      service_auth_state: 'service_unavailable',
    };
    api.startWebVPNPasswordLogin.mockResolvedValue(result);
    await render();
    await settle(() => login.current.open('password'));
    await settle(() => fill(document.querySelector('input[placeholder="密码"]'), 'fixture-only'));
    await settle(() => click(button('恢复 WebVPN 登录')));
    expect(done).toHaveBeenCalledWith(result);
    expect(container.querySelector('[data-testid="sms"]')).toBeNull();
  });

  test('a late password response cannot clear loading on a newer login', async () => {
    let finishOld, finishNew;
    api.startWebVPNPasswordLogin
      .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }))
      .mockReturnValueOnce(new Promise(resolve => { finishNew = resolve; }));
    await render();
    await settle(() => login.current.open('password'));
    await settle(() => fill(document.querySelector('input[placeholder="密码"]'), 'fixture-only'));
    await settle(() => click(button('恢复 WebVPN 登录')));
    await settle(() => login.current.close());
    await settle(() => login.current.open('password'));
    await settle(() => fill(document.querySelector('input[placeholder="密码"]'), 'fixture-only'));
    await settle(() => click(button('恢复 WebVPN 登录')));
    await settle(() => finishOld({ success: false, message: 'old failure' }));
    expect(button('恢复 WebVPN 登录').classList.contains('ant-btn-loading')).toBe(true);
    await settle(() => finishNew({ success: false, message: 'new failure' }));
    expect(button('恢复 WebVPN 登录').classList.contains('ant-btn-loading')).toBe(false);
    expect(document.body.textContent).toContain('new failure');
    expect(document.body.textContent).not.toContain('old failure');
  });

  test('offline mode never starts or resumes authentication', async () => {
    await render('cxcy', true);
    await settle(() => login.current.open('qr'));
    expect(api.getPendingAuthChallenge).not.toHaveBeenCalled();
    expect(api.startWebVPNQRLogin).not.toHaveBeenCalled();
    expect(hasServiceAuthOwner('cxcy')).toBe(false);
  });

  test('network failure offers a route change without treating it as no activities', async () => {
    const change = jest.fn();
    await settle(() => root.render(<ServiceAuthNotice status={{
      effective_network_mode: 'direct', service_auth_state: 'network_unreachable',
      message: '无法直连创院系统',
    }} onChange={change} />));
    expect(container.textContent).toContain('无法直连创院系统');
    await settle(() => click(button('切换 WebVPN')));
    expect(change).toHaveBeenCalledWith('webvpn');
  });
});
