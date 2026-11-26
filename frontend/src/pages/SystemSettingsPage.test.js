import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import SystemSettingsPage from './SystemSettingsPage';
import {
  getAuthRecoverySettings, updateAuthRecoverySettings,
  getAdminStatus, getAdminAccounts, getSystemCacheSettings, getSystemMailSettings, updateSystemCacheSettings,
} from '../services/api';

jest.mock('./LogsPage', () => () => null);
jest.mock('../resources/ResourceStore', () => ({ useResourceIdentity: () => 'student' }));
jest.mock('../resources/BrowserTimetableStore', () => ({
  clearBrowserAvatarCache: jest.fn(), clearBrowserTimetableCache: jest.fn(),
}));
jest.mock('../services/api', () => ({
  getAuthRecoverySettings: jest.fn(),
  getAdminStatus: jest.fn(),
  getAdminAccounts: jest.fn(),
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

  test('shows actual freshness behavior and saves account cache settings', async () => {
    const cache = { scores: { enabled: false, interval_minutes: 60 } };
    getSystemCacheSettings.mockResolvedValue({ cache });
    updateSystemCacheSettings.mockResolvedValue({ cache });
    await render({ public_base_url: '', link_ttl_hours: 3 });
    expect(container.textContent).toContain('快照仍保留用于离线查看');
    expect(container.textContent).toContain('仅影响当前账号');
    const save = Array.from(container.querySelectorAll('button')).find(button => button.textContent.includes('保存缓存配置'));
    await act(async () => {
      save.click();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    expect(updateSystemCacheSettings).toHaveBeenCalledWith({ resources: cache });
  });

  test('shows management only after the server verifies the configured school account', async () => {
    getAuthRecoverySettings.mockResolvedValue({});
    const page = identity => <MemoryRouter initialEntries={['/system-settings?tab=admin']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <SystemSettingsPage adminIdentity={identity} />
    </MemoryRouter>;
    getAdminStatus.mockResolvedValue({ eligible: false, configured: true, authenticated: false });
    await act(async () => root.render(page('ordinary')));
    expect(container.textContent).not.toContain('站点管理');
    expect(getAdminAccounts).not.toHaveBeenCalled();
    getAdminStatus.mockResolvedValue({ eligible: true, configured: true, authenticated: false });
    await act(async () => root.render(page('owner')));
    expect(container.textContent).toContain('解锁站点管理');
    expect(getAdminAccounts).not.toHaveBeenCalled();
    await act(async () => root.render(page('')));
    expect(container.textContent).not.toContain('站点管理');
  });
});
