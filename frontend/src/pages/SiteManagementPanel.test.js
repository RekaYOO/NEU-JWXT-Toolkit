import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SiteManagementPanel from './SiteManagementPanel';
import { changeAdminAccount, getAdminAccounts } from '../services/api';

jest.mock('../services/api', () => ({
  getAdminAccounts: jest.fn(), addAdminAccount: jest.fn(), changeAdminAccount: jest.fn(),
}));

describe('管理员账号边界', () => {
  let container;
  let root;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    getAdminAccounts.mockResolvedValue({ accounts: [{ account: '20990001', enabled: true }] });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });
  const render = async () => act(async () => { root.render(<SiteManagementPanel />); });
  const click = async label => act(async () => {
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.replace(/\s/g, '') === label);
    expect(button).toBeTruthy();
    button.click();
  });

  test('管理面板直接读取名单且无需额外登录', async () => {
    await render();
    expect(getAdminAccounts).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('20990001');
    expect(container.querySelector('input[type="password"]')).toBeNull();
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
    expect(container.textContent).toContain('请先验证管理员身份');
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });
});
