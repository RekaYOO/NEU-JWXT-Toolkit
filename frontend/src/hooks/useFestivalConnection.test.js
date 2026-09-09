import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import useFestivalConnection from './useFestivalConnection';
import { getFestivalServiceStatus, updateFestivalSettings } from '../services/api';

jest.mock('../services/api', () => ({
  getFestivalServiceStatus: jest.fn(), updateFestivalSettings: jest.fn(),
}));

describe('CXCY connection request ordering', () => {
  let root, container, connection;
  function Probe({ offline = false }) {
    connection = useFestivalConnection(offline);
    return <span>{connection.status?.network_mode}</span>;
  }
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });
  test('a late old probe cannot overwrite a saved new route', async () => {
    let finishOld;
    getFestivalServiceStatus.mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }));
    getFestivalServiceStatus.mockResolvedValue({ network_mode: 'webvpn', service_authenticated: true });
    updateFestivalSettings.mockResolvedValue({ network_mode: 'webvpn', service_auth_state: 'checking' });
    await act(async () => root.render(<Probe />));
    await act(async () => { await connection.change('webvpn'); });
    await act(async () => { finishOld({ network_mode: 'direct' }); });
    expect(container.textContent).toBe('webvpn');
    expect(connection.busy).toBe(false);
  });
  test('offline entry does not probe or persist route changes', async () => {
    await act(async () => root.render(<Probe offline />));
    await act(async () => { await connection.change('direct'); });
    expect(getFestivalServiceStatus).not.toHaveBeenCalled();
    expect(updateFestivalSettings).not.toHaveBeenCalled();
  });

  test('gateway success with a service outage invalidates old login-required probes', async () => {
    let finish;
    getFestivalServiceStatus.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    await act(async () => root.render(<Probe />));
    await act(async () => connection.unavailable());
    await act(async () => finish({ service_auth_state: 'login_required' }));
    expect(connection.status.service_auth_state).toBe('service_unavailable');
    expect(connection.status.message).toContain('无需重复提交短信');
    expect(getFestivalServiceStatus).toHaveBeenCalledTimes(1);
  });

  test('an old post-save probe cannot clear the next route probe loading state', async () => {
    let finishOld, finishNew;
    getFestivalServiceStatus.mockResolvedValueOnce({ network_mode: 'follow' })
      .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }))
      .mockReturnValueOnce(new Promise(resolve => { finishNew = resolve; }));
    updateFestivalSettings.mockImplementation(async network_mode => ({ network_mode }));
    await act(async () => root.render(<Probe />));
    let oldChange, newChange;
    await act(async () => { oldChange = connection.change('direct'); });
    expect(connection.saving).toBe(false);
    await act(async () => { newChange = connection.change('webvpn'); });
    await act(async () => { finishOld({ network_mode: 'direct' }); await oldChange; });
    expect(connection.busy).toBe(true);
    expect(container.textContent).toBe('webvpn');
    await act(async () => { finishNew({ network_mode: 'webvpn' }); await newChange; });
    expect(connection.busy).toBe(false);
  });

  test('a save failure after switching offline cannot overwrite connection state', async () => {
    let failSave;
    getFestivalServiceStatus.mockResolvedValue({ network_mode: 'follow' });
    updateFestivalSettings.mockReturnValue(new Promise((resolve, reject) => { failSave = reject; }));
    await act(async () => root.render(<Probe />));
    let changing;
    await act(async () => { changing = connection.change('webvpn'); });
    await act(async () => root.render(<Probe offline />));
    await act(async () => { failSave(new Error('late failure')); await changing; });
    expect(connection.status.message).toBeUndefined();
    expect(connection.busy).toBe(false);
    expect(connection.saving).toBe(false);
    expect(getFestivalServiceStatus).toHaveBeenCalledTimes(1);
  });
});
