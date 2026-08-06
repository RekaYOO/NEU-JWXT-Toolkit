import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import LoginPage from './LoginPage';
import { checkStatus, getOfflineStatus } from '../services/api';
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
}));

describe('LoginPage session recovery boundary', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    sessionStorage.clear();
    checkStatus.mockReset();
    checkStatus.mockResolvedValue({ is_logged_in: false, current_user: null });
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
});
