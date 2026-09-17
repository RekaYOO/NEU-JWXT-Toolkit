import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import ReliableRoute from './ReliableRoute';
import { loadRouteComponent, loadedRouteComponent, retryRouteComponent } from '../utils/routeModules';
import { isChunkLoadError, recoverFromStaleBuild } from '../utils/routeLoadRecovery';

jest.mock('../utils/routeModules', () => ({
  loadRouteComponent: jest.fn(),
  loadedRouteComponent: jest.fn(),
  retryRouteComponent: jest.fn(),
}));
jest.mock('../utils/routeLoadRecovery', () => ({
  isChunkLoadError: jest.fn(error => /Loading chunk/.test(error?.message || '')),
  recoverFromStaleBuild: jest.fn().mockResolvedValue(false),
}));

describe('ReliableRoute', () => {
  let container;
  let root;
  beforeEach(() => {
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    loadedRouteComponent.mockReturnValue(null);
    isChunkLoadError.mockImplementation(error => /Loading chunk/.test(error?.message || ''));
    recoverFromStaleBuild.mockResolvedValue(false);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  test('renders the loaded page without replacing the application shell', async () => {
    const Page = () => <div>异步页面已加载</div>;
    loadRouteComponent.mockResolvedValue(Page);
    await act(async () => {
      root.render(<ReliableRoute routeId="evaluation" />);
      await Promise.resolve();
    });
    expect(container.textContent).toContain('异步页面已加载');
    expect(container.textContent).not.toContain('页面资源加载失败');
  });

  test('retries a transient chunk failure once and then shows an actionable error', async () => {
    let retryStarted;
    const retryStartedPromise = new Promise(resolve => { retryStarted = resolve; });
    const timeout = jest.spyOn(window, 'setTimeout').mockImplementation(callback => {
      callback();
      return 1;
    });
    loadRouteComponent.mockRejectedValue(new Error('Loading chunk 5 failed'));
    retryRouteComponent.mockImplementation(() => {
      retryStarted();
      return Promise.reject(new Error('Loading chunk 5 failed'));
    });
    await act(async () => {
      root.render(<ReliableRoute routeId="evaluation" />);
    });
    await act(async () => {
      await retryStartedPromise;
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(recoverFromStaleBuild).toHaveBeenCalledWith({ error: expect.any(Error) });
    expect(retryRouteComponent).toHaveBeenCalledWith('evaluation');
    expect(container.textContent).toContain('页面资源加载失败');
    expect(container.textContent).toContain('重试加载');
    timeout.mockRestore();
  });

  test('manual retry starts a fresh load after a persistent resource failure', async () => {
    loadRouteComponent
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce(() => <div>重试后页面</div>);
    await act(async () => {
      root.render(<ReliableRoute routeId="evaluation" />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.textContent).toContain('页面资源加载失败');
    await act(async () => {
      Array.from(container.querySelectorAll('button'))
        .find(button => button.textContent === '重试加载')
        .click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(loadRouteComponent).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('重试后页面');
  });

  test('render failures stay inside the render boundary without chunk recovery', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const BrokenPage = () => { throw new Error('render failed'); };
    loadRouteComponent.mockResolvedValue(BrokenPage);
    await act(async () => {
      root.render(<ReliableRoute routeId="evaluation" />);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.textContent).toContain('页面渲染失败');
    expect(isChunkLoadError).not.toHaveBeenCalled();
    expect(recoverFromStaleBuild).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
