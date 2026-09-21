import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import VersionUpdateCard from './VersionUpdateCard';
import { getRuntimeUpdate, startRuntimeUpdateDownload } from '../services/api';
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
});
