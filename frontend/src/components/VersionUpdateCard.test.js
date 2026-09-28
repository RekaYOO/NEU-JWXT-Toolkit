import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Modal } from 'antd';
import VersionUpdateCard from './VersionUpdateCard';
import { getRuntimeUpdate, getRuntimeUpdateJob, startRuntimeUpdateDownload } from '../services/api';
import { checkNativeAppUpdate, nativeShellInfo } from '../services/nativeBridge';

jest.mock('../services/api', () => ({
  getRuntimeUpdate: jest.fn(),
  startRuntimeUpdateDownload: jest.fn(),
  getRuntimeUpdateJob: jest.fn(),
}));
jest.mock('../services/nativeBridge', () => ({
  nativeShellInfo: jest.fn(() => null),
  checkNativeAppUpdate: jest.fn(),
  installNativeAppUpdate: jest.fn(),
}));

describe('VersionUpdateCard', () => {
  let root;
  let container;
  beforeAll(() => { global.IS_REACT_ACT_ENVIRONMENT = true; });
  beforeEach(() => {
    jest.clearAllMocks();
    nativeShellInfo.mockReturnValue(null);
    getRuntimeUpdate.mockResolvedValue({
      current_version: '1.0.0', latest_version: '1.0.0', update_available: false,
      capability: 'none', release_url: 'https://github.com/RekaYOO/NEU-JWXT-Toolkit/releases/latest',
    });
    startRuntimeUpdateDownload.mockResolvedValue({ job_id: 'job', state: 'downloaded' });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  test('shows current and latest versions', async () => {
    await act(async () => root.render(<VersionUpdateCard />));
    expect(getRuntimeUpdate).not.toHaveBeenCalled();
    await act(async () => container.querySelector('button').click());
    expect(container.textContent).toContain('当前版本：1.0.0');
    expect(container.textContent).toContain('已是最新');
  });

  test('shows a release-check error instead of claiming the app is current', async () => {
    getRuntimeUpdate.mockResolvedValueOnce({
      current_version: '1.0.0', latest_version: null, update_available: false,
      capability: 'none', error: '无法连接 GitHub Release',
    });
    await act(async () => root.render(<VersionUpdateCard />));
    await act(async () => container.querySelector('button').click());
    expect(container.textContent).toContain('无法连接 GitHub Release');
    expect(container.textContent).not.toContain('已是最新');
  });

  test('client checks both its APK and the connected server only after a click', async () => {
    nativeShellInfo.mockReturnValue({ kind: 'client', version: '1.0.0' });
    checkNativeAppUpdate.mockResolvedValue({ current_version: '1.0.0', latest_version: '1.1.0', available: true });
    getRuntimeUpdate.mockResolvedValue({ current_version: '2.0.0', latest_version: '2.1.0', update_available: true, capability: 'linux-auto' });
    await act(async () => root.render(<VersionUpdateCard />));
    expect(checkNativeAppUpdate).not.toHaveBeenCalled();
    expect(getRuntimeUpdate).not.toHaveBeenCalled();
    await act(async () => container.querySelector('button').click());
    expect(checkNativeAppUpdate).toHaveBeenCalledWith();
    expect(getRuntimeUpdate).toHaveBeenCalledWith(true);
    expect(container.textContent).toContain('下载并安装客户端');
    expect(container.textContent).toContain('下载并升级服务端');
  });

  test('client shows the server result while the APK check is still pending', async () => {
    nativeShellInfo.mockReturnValue({ kind: 'client', version: '1.0.0' });
    let resolveApp;
    checkNativeAppUpdate.mockReturnValue(new Promise(resolve => { resolveApp = resolve; }));
    getRuntimeUpdate.mockResolvedValue({
      current_version: '1.0.0', latest_version: '2.0.0',
      update_available: true, capability: 'linux-auto',
    });
    await act(async () => root.render(<VersionUpdateCard />));
    await act(async () => container.querySelector('button').click());
    expect(container.querySelector('[aria-label="连接的服务端更新"]').textContent)
      .toContain('最新版本：2.0.0');
    await act(async () => resolveApp({ available: false }));
  });

  test('client shows the server failure history and permits another manual attempt', async () => {
    nativeShellInfo.mockReturnValue({ kind: 'client', version: '1.0.0' });
    checkNativeAppUpdate.mockResolvedValue({ available: false });
    getRuntimeUpdate.mockResolvedValue({
      current_version: '1.0.0', latest_version: '2.0.0',
      update_available: true, capability: 'linux-auto',
      job: {
        job_id: 'a'.repeat(32), state: 'failed', error: '更新器未接管任务',
        history: [
          { state: 'requested', at: '2026-09-28T10:00:00Z', message: '等待服务端更新器接管' },
          { state: 'failed', at: '2026-09-28T10:02:00Z', message: '更新失败' },
        ],
      },
    });
    await act(async () => root.render(<VersionUpdateCard />));
    await act(async () => container.querySelector('button').click());
    const panel = container.querySelector('[aria-label="连接的服务端更新"]');
    expect(panel.querySelector('[aria-label="更新过程"] li')).not.toBeNull();
    expect(panel.textContent).toContain('更新器未接管任务');
    expect(panel.querySelector('button').disabled).toBe(false);
    expect(startRuntimeUpdateDownload).not.toHaveBeenCalled();
  });

  test('client distinguishes downloaded-only server packages from an installed update', async () => {
    nativeShellInfo.mockReturnValue({ kind: 'client', version: '1.0.0' });
    checkNativeAppUpdate.mockResolvedValue({ available: false });
    getRuntimeUpdate.mockResolvedValue({
      current_version: '1.0.0', latest_version: '2.0.0',
      update_available: true, capability: 'linux-manual',
      job: { job_id: 'a'.repeat(32), state: 'downloaded', path: '/var/lib/neu-jwxt-toolkit/updates/2.0.0/package.tar.gz' },
    });
    await act(async () => root.render(<VersionUpdateCard />));
    await act(async () => container.querySelector('button').click());
    const panel = container.querySelector('[aria-label="连接的服务端更新"]');
    expect(panel.textContent).toContain('更新包已下载，请手动安装');
    expect(panel.textContent).toContain('自动更新器未运行');
    expect(panel.textContent).not.toContain('更新已完成');
  });

  test('client accepts a server job without claiming success or polling it twice', async () => {
    jest.useFakeTimers();
    const confirm = jest.spyOn(Modal, 'confirm');
    let approve;
    confirm.mockImplementation(options => { approve = options.onOk; return { destroy: jest.fn() }; });
    try {
      nativeShellInfo.mockReturnValue({ kind: 'client', version: '1.0.0' });
      checkNativeAppUpdate.mockResolvedValue({ available: false });
      getRuntimeUpdate.mockResolvedValue({
        current_version: '1.0.0', latest_version: '2.0.0',
        update_available: true, capability: 'linux-auto',
      });
      startRuntimeUpdateDownload.mockResolvedValue({
        job_id: 'a'.repeat(32), state: 'downloading', version: '2.0.0',
        bytes_downloaded: 25, bytes_total: 100,
        history: [{ state: 'downloading', at: '2026-09-28T10:00:00Z', message: '正在下载更新包' }],
      });
      getRuntimeUpdateJob.mockResolvedValue({
        job_id: 'a'.repeat(32), state: 'failed', error: '更新器校验失败',
      });
      await act(async () => root.render(<VersionUpdateCard />));
      await act(async () => container.querySelector('button').click());
      const panel = container.querySelector('[aria-label="连接的服务端更新"]');
      await act(async () => panel.querySelector('button').click());
      await act(async () => approve());
      expect(panel.textContent).toContain('已下载 25%');
      expect(panel.textContent).not.toContain('更新已完成');
      await act(async () => {
        jest.advanceTimersByTime(2000);
        await Promise.resolve();
      });
      expect(getRuntimeUpdateJob).toHaveBeenCalledTimes(1);
      expect(panel.textContent).toContain('更新器校验失败');
    } finally {
      confirm.mockRestore();
      jest.useRealTimers();
    }
  });
});
