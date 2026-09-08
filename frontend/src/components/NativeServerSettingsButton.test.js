import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import NativeServerSettingsButton from './NativeServerSettingsButton';
import AccessLoginPage from '../pages/AccessLoginPage';
import LoginPage from '../pages/LoginPage';
import { getOfflineStatus, checkStatus } from '../services/api';

jest.mock('../services/api', () => ({
  getOfflineStatus: jest.fn(() => new Promise(() => {})),
  checkStatus: jest.fn(),
  loginAccessGateway: jest.fn(),
}));

describe('Native server settings before authentication', () => {
  let container;
  let root;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    getOfflineStatus.mockImplementation(() => new Promise(() => {}));
    checkStatus.mockResolvedValue({ is_logged_in: false });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    window.matchMedia = jest.fn(() => ({
      matches: false, addListener: jest.fn(), removeListener: jest.fn(),
    }));
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    delete window.NeuNative;
  });

  test.each(['browser', 'local'])('%s does not expose remote settings', async kind => {
    if (kind === 'local') window.NeuNative = { getShellInfo: () => JSON.stringify({ kind }) };
    await act(async () => root.render(<NativeServerSettingsButton />));
    expect(container.querySelector('button')).toBeNull();
  });

  test.each(['button', 'access', 'pending-login'])('client can change server from %s', async page => {
    const openServerSettings = jest.fn();
    window.NeuNative = {
      getShellInfo: () => JSON.stringify({ kind: 'client' }),
      openServerSettings,
    };
    const content = page === 'access'
      ? <AccessLoginPage configured onSuccess={jest.fn()} />
      : page === 'pending-login'
        ? <LoginPage onLoginSuccess={jest.fn()} onOfflineSuccess={jest.fn()} />
        : <NativeServerSettingsButton />;
    await act(async () => root.render(content));
    if (page === 'pending-login') expect(container.textContent).toContain('正在检查登录状态');
    const button = [...container.querySelectorAll('button')]
      .find(element => element.textContent.includes('服务端设置'));
    expect(button).toBeDefined();
    await act(async () => button.click());
    expect(openServerSettings).toHaveBeenCalledTimes(1);
  });
});
