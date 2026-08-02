import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import MainLayout from './MainLayout';
import { shutdownRuntime } from '../services/api';

jest.mock('../services/api', () => ({
  getUserAvatar: jest.fn().mockResolvedValue(null),
  logout: jest.fn().mockResolvedValue({ success: true }),
  shutdownRuntime: jest.fn().mockResolvedValue({ success: true }),
}));

const renderLayout = async (runtimeProfile) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter
        initialEntries={['/scores']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route
            path="/"
            element={(
              <MainLayout
                runtimeProfile={runtimeProfile}
                userInfo="测试用户"
                onLogout={jest.fn()}
              />
            )}
          >
            <Route path="scores" element={<div>成绩页面</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  });
  return {
    container,
    unmount: async () => {
      await act(async () => root.unmount());
      container.remove();
    },
  };
};

const click = async (element) => {
  await act(async () => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 0));
  });
};

describe('MainLayout desktop lifecycle controls', () => {
  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  beforeEach(() => {
    shutdownRuntime.mockReset();
    shutdownRuntime.mockResolvedValue({ success: true });
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

  test('shows a dedicated exit button only for the desktop runtime', async () => {
    const desktop = await renderLayout('desktop');
    expect(desktop.container.querySelector('button[aria-label="退出桌面程序"]')).toBeTruthy();
    await desktop.unmount();

    const development = await renderLayout('development');
    expect(development.container.querySelector('button[aria-label="退出桌面程序"]')).toBeNull();
    await development.unmount();
  });

  test('requires confirmation before requesting graceful shutdown', async () => {
    const page = await renderLayout('desktop');
    await click(page.container.querySelector('button[aria-label="退出桌面程序"]'));
    expect(document.body.textContent).toContain('退出桌面程序？');
    expect(shutdownRuntime).not.toHaveBeenCalled();

    const confirm = [...document.querySelectorAll('button')].find(
      button => button.textContent.trim() === '退出程序',
    );
    await click(confirm);
    expect(shutdownRuntime).toHaveBeenCalledTimes(1);
    expect(page.container.textContent).toContain('本地服务已退出');
    await page.unmount();
  });
});
