import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import AdminPage from './AdminPage';
import { changeAdminAccount, getAdminAccounts, getAdminStatus, logoutAdmin, logoutAccessGateway } from '../services/api';

jest.mock('../services/api', () => ({
  getAdminStatus: jest.fn(), getAdminAccounts: jest.fn(), loginAdmin: jest.fn(),
  logoutAdmin: jest.fn(), logoutAccessGateway: jest.fn(), addAdminAccount: jest.fn(), changeAdminAccount: jest.fn(),
}));

describe('管理员账号边界', () => {
  let container;
  let root;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    getAdminStatus.mockResolvedValue({ configured: true, authenticated: true, deployment_mode: 'remote-multi' });
    getAdminAccounts.mockResolvedValue({ accounts: [{ account: '20990001', enabled: true }] });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });
  const render = async () => act(async () => { root.render(<AdminPage />); });
  const click = async label => act(async () => {
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.replace(/\s/g, '') === label);
    expect(button).toBeTruthy();
    button.click();
  });

  test('未验证管理员身份不读取名单，密码使用独立自动填充分区', async () => {
    getAdminStatus.mockResolvedValue({ configured: true, authenticated: false });
    await render();
    expect(getAdminAccounts).not.toHaveBeenCalled();
    expect(container.querySelector('input[type="password"]').autocomplete).toBe('section-toolkit-admin current-password');
  });

  test('停止操作确认后才提交，未知失败不显示成功', async () => {
    changeAdminAccount.mockRejectedValue(new Error('请求未完成，请刷新核对'));
    await render();
    await click('停止运行');
    expect(changeAdminAccount).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('自动任务仍需手动开启');
    await click('确认');
    expect(changeAdminAccount).toHaveBeenCalledWith('20990001', 'stop');
    expect(container.textContent).toContain('请求未完成，请刷新核对');
    expect(container.textContent).not.toContain('停止运行成功');
  });

  test('名单读取失去权限立即清空原有账号', async () => {
    await render();
    expect(container.textContent).toContain('20990001');
    getAdminAccounts.mockRejectedValue({ message: '请先验证管理员身份', response: { status: 401 } });
    await click('刷新列表');
    expect(container.textContent).not.toContain('20990001');
    expect(container.querySelector('input[type="password"]')).toBeTruthy();
  });

  test('退出管理清空名单，但不操作学校登录', async () => {
    logoutAdmin.mockResolvedValue({ success: true });
    await render();
    await click('退出管理');
    expect(logoutAdmin).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain('20990001');
    expect(container.textContent).toContain('已退出管理');
  });

  test('站点退出失败保留名单并允许重试', async () => {
    logoutAccessGateway.mockRejectedValueOnce(new Error('暂时无法退出'));
    await act(async () => root.render(<AdminPage siteAccessRequired />));
    await click('退出站点');
    expect(logoutAccessGateway).toHaveBeenCalledTimes(1);
    expect(logoutAdmin).not.toHaveBeenCalled();
    expect(container.textContent).toContain('暂时无法退出');
    expect(container.textContent).toContain('20990001');
    expect([...container.querySelectorAll('button')].find(button => button.textContent === '退出站点').disabled).toBe(false);
  });
});
