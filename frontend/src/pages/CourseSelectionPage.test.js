import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import CourseSelectionPage from './CourseSelectionPage';
import { getJwxkCatalogArchives, getJwxkStatus, updateJwxkSettings } from '../services/api';

jest.mock('../services/api', () => ({
  getJwxkStatus: jest.fn(),
  getJwxkCatalogArchives: jest.fn(),
  updateJwxkSettings: jest.fn(),
  isWebVPNCampusNetworkBlocked: jest.fn(() => false),
}));
let mockAuthCallbacks;
jest.mock('../components/ServiceConnection', () => ({
  ...jest.requireActual('../components/ServiceConnection'),
  ServiceWebVPNLogin: require('react').forwardRef((props, ref) => {
    mockAuthCallbacks = props;
    require('react').useImperativeHandle(ref, () => ({ close: jest.fn(), open: jest.fn() }));
    return null;
  }),
}));
jest.mock('../components/WebVPNAuthModal', () => () => null);
jest.mock('../services/nativeBridge', () => ({ nativeShellInfo: () => ({ kind: 'client' }) }));

describe('CourseSelectionPage mobile recovery notice', () => {
  let container;
  let root;
  const localStatus = {
    network_mode: 'follow', effective_network_mode: 'webvpn',
    primary_authenticated: true, service_authenticated: false,
    service_auth_state: 'checking', message: '正在核验选课系统会话', batches: [],
  };
  const render = async () => act(async () => {
    root.render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <CourseSelectionPage />
      </MemoryRouter>,
    );
  });
  const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  };

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.matchMedia = jest.fn(() => ({
      matches: false,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
    }));
    getJwxkCatalogArchives.mockResolvedValue({ archives: [] });
    getJwxkStatus.mockResolvedValue({
      service_authenticated: false,
      primary_authenticated: true,
      service_auth_state: 'login_required',
      network_mode: 'webvpn',
      effective_network_mode: 'webvpn',
      message: '选课系统的 WebVPN 登录已失效，请使用账号密码或微信扫码恢复。',
      batches: [],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('keeps the message in its own column and places recovery actions after it', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <CourseSelectionPage />
        </MemoryRouter>,
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const alert = container.querySelector('.course-selection-auth-alert');
    const content = alert?.querySelector('.ant-alert-content');
    const action = alert?.querySelector('.ant-alert-action');
    expect(alert).not.toBeNull();
    expect(content?.textContent).toContain('WebVPN 登录已失效');
    expect(action?.textContent).toContain('账号密码恢复');
    expect(action?.textContent).toContain('微信扫码恢复');
    expect(content.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test('shows the resolved follow route before the remote probe finishes', async () => {
    const probe = deferred();
    getJwxkStatus.mockResolvedValueOnce(localStatus).mockReturnValueOnce(probe.promise);
    await render();
    expect(getJwxkStatus).toHaveBeenNthCalledWith(1, { params: { probe: false }, timeout: 5000 });
    expect(container.querySelector('.service-route-control').textContent).toContain('当前有效线路：WebVPN（正在核验）');
    expect(container.querySelector('input[value="follow"]').checked).toBe(true);
    await act(async () => probe.resolve({ ...localStatus, service_auth_state: 'not_in_selection_round', message: '当前未开放选课' }));
    expect(container.textContent).toContain('当前未开放选课');
    expect(container.querySelector('.service-route-control').textContent).not.toContain('正在核验');
  });

  test('probe timeout preserves the resolved route and ends loading', async () => {
    const probe = deferred();
    getJwxkStatus.mockResolvedValueOnce(localStatus).mockReturnValueOnce(probe.promise);
    await render();
    await act(async () => probe.reject(Object.assign(new Error('timeout'), { code: 'ECONNABORTED' })));
    expect(container.textContent).toContain('状态核验超时');
    expect(container.querySelector('.service-route-control').textContent).toContain('当前有效线路：WebVPN');
    expect(container.querySelector('.service-route-control').textContent).not.toContain('正在核验');
    expect(container.textContent).not.toContain('账号密码恢复');
  });

  test.each(['not_in_selection_round', 'service_unavailable'])(
    'keeps authenticated service result %s and ignores an earlier expired response',
    async state => {
      const probe = deferred();
      getJwxkStatus.mockResolvedValueOnce(localStatus).mockReturnValueOnce(probe.promise);
      await render();
      await act(async () => mockAuthCallbacks.onAuthenticated({
        success: true, status: 'authenticated', service_auth_state: state,
      }));
      await act(async () => probe.resolve({
        ...localStatus, service_auth_state: 'login_required', message: '过期的登录失效结果',
      }));
      expect(getJwxkStatus).toHaveBeenCalledTimes(2);
      expect(container.textContent).not.toContain('过期的登录失效结果');
      expect(container.textContent).not.toContain('账号密码恢复');
      expect(container.textContent).toContain(
        state === 'not_in_selection_round' ? '不在学校开放的选课轮次中' : '无需重复认证',
      );
    },
  );

  test('route change ignores the old probe result', async () => {
    const oldProbe = deferred();
    getJwxkStatus.mockResolvedValueOnce(localStatus).mockReturnValueOnce(oldProbe.promise)
      .mockResolvedValueOnce({ ...localStatus, network_mode: 'direct', effective_network_mode: 'direct', service_authenticated: true });
    updateJwxkSettings.mockResolvedValue({ ...localStatus, network_mode: 'direct', effective_network_mode: 'direct' });
    await render();
    await act(async () => container.querySelector('input[value="direct"]').click());
    await act(async () => oldProbe.resolve({
      ...localStatus, service_auth_state: 'login_required', message: '旧线路结果',
    }));
    expect(updateJwxkSettings).toHaveBeenCalledWith('direct');
    expect(container.querySelector('.service-route-control').textContent).toContain('当前有效线路：直连');
    expect(container.textContent).not.toContain('旧线路结果');
    expect(container.querySelector('.service-route-control').textContent).not.toContain('正在核验');
  });

  test('a failed route save ends the superseded probe loading state', async () => {
    const oldProbe = deferred();
    getJwxkStatus.mockResolvedValueOnce(localStatus).mockReturnValueOnce(oldProbe.promise);
    updateJwxkSettings.mockRejectedValue(new Error('network'));
    await render();
    await act(async () => container.querySelector('input[value="direct"]').click());
    expect(container.textContent).toContain('线路设置未能确认');
    expect(container.querySelector('.service-route-control').textContent).not.toContain('正在核验');
    await act(async () => oldProbe.resolve(localStatus));
    expect(container.textContent).toContain('线路设置未能确认');
  });

  test('blocks batch refresh while the route preference is being saved', async () => {
    const saved = deferred();
    updateJwxkSettings.mockReturnValueOnce(saved.promise);
    await render();
    await act(async () => container.querySelector('input[value="direct"]').click());
    const refresh = container.querySelector('.course-selection-heading button');
    expect(refresh.disabled).toBe(true);
    const requestCount = getJwxkStatus.mock.calls.length;
    await act(async () => refresh.click());
    expect(getJwxkStatus).toHaveBeenCalledTimes(requestCount);
    await act(async () => saved.reject(new Error('network')));
    expect(refresh.disabled).toBe(false);
  });

  test('campus restriction cannot be overwritten by an earlier probe', async () => {
    const probe = deferred();
    getJwxkStatus.mockResolvedValueOnce(localStatus).mockReturnValueOnce(probe.promise);
    await render();
    await act(async () => mockAuthCallbacks.onBlocked());
    await act(async () => probe.resolve(localStatus));
    expect(container.textContent).toContain('校园网环境下学校 WebVPN 不可用');
    expect(container.querySelector('.service-route-control').textContent).not.toContain('正在核验');
  });
});
