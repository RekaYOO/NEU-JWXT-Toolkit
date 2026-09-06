import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import SystemSettingsPage from './SystemSettingsPage';
import {
  getAuthRecoverySettings, updateAuthRecoverySettings,
  getSystemCacheSettings, getSystemMailSettings,
} from '../services/api';

jest.mock('./LogsPage', () => () => null);
jest.mock('../resources/ResourceStore', () => ({ useResourceIdentity: () => 'student' }));
jest.mock('../resources/BrowserTimetableStore', () => ({
  clearBrowserAvatarCache: jest.fn(), clearBrowserTimetableCache: jest.fn(),
}));
jest.mock('../services/api', () => ({
  getAuthRecoverySettings: jest.fn(),
  updateAuthRecoverySettings: jest.fn(),
  getSystemCacheSettings: jest.fn().mockResolvedValue({ cache: {} }),
  getSystemMailSettings: jest.fn().mockResolvedValue({}),
  testSystemMail: jest.fn(),
  updateSystemCacheSettings: jest.fn(),
  updateSystemMailSettings: jest.fn(),
}));

describe('recovery link lifetime settings', () => {
  let container;
  let root;
  beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
  beforeEach(() => {
    jest.clearAllMocks();
    getSystemCacheSettings.mockResolvedValue({ cache: {} });
    getSystemMailSettings.mockResolvedValue({});
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });
  const render = async config => {
    getAuthRecoverySettings.mockResolvedValue(config);
    await act(async () => root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <SystemSettingsPage />
      </MemoryRouter>
    ));
  };
  const submit = async () => {
    const input = container.querySelector('#link_ttl_hours');
    await act(async () => {
      input.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await new Promise(resolve => setTimeout(resolve, 0));
    });
  };

  test('defaults to three hours when loading an older configuration', async () => {
    await render({ public_base_url: 'https://toolkit.example.com' });
    expect(container.querySelector('#link_ttl_hours').value).toBe('3');
    expect(container.textContent).toContain('恢复链接有效期（小时）');
  });

  test('loads and saves the duration with the existing recovery URL', async () => {
    const config = { public_base_url: 'https://toolkit.example.com', link_ttl_hours: 6 };
    await render(config);
    expect(container.querySelector('#link_ttl_hours').value).toBe('6');
    updateAuthRecoverySettings.mockResolvedValue({ success: true, config: { ...config, link_ttl_hours: 4 } });
    const input = container.querySelector('#link_ttl_hours');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '4');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await submit();
    expect(updateAuthRecoverySettings).toHaveBeenCalledWith({ ...config, link_ttl_hours: 4 });
    expect(input.value).toBe('4');
  });

  test('does not submit an empty duration', async () => {
    await render({ public_base_url: '', link_ttl_hours: 3 });
    const input = container.querySelector('#link_ttl_hours');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await submit();
    expect(updateAuthRecoverySettings).not.toHaveBeenCalled();
    expect(container.textContent).toContain('请输入 1 到 168 小时的整数');
  });
});
