import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { getAccessStatus, getClientBootstrap } from './services/api';

jest.mock('./services/api', () => ({
  getAccessStatus: jest.fn(),
  getClientBootstrap: jest.fn(),
}));
jest.mock('./layouts/MainLayout', () => {
  const { Outlet } = require('react-router-dom');
  return () => <Outlet />;
});
jest.mock('./pages/TimetablePage', () => {
  const { useResourceRecoveryMode } = require('./resources/ResourceStore');
  return () => <div data-testid="timetable">{useResourceRecoveryMode() ? '只读课表' : '在线课表'}</div>;
});

describe('cached timetable while the native backend is still starting', () => {
  let root;
  let container;
  beforeEach(() => {
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    sessionStorage.clear();
    localStorage.clear();
    window.NeuNative = { getShellInfo: () => JSON.stringify({ kind: 'local', server_url: '本机' }) };
    getAccessStatus.mockReturnValue(new Promise(() => {}));
    localStorage.setItem('neu-toolbox-timetable-recovery-namespace', 'account:abc123');
    localStorage.setItem('neu_toolbox:defaultTimetableOnOpen', 'true');
    window.history.replaceState({}, '', '/');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    sessionStorage.clear();
    localStorage.clear();
    delete window.NeuNative;
    global.IS_REACT_ACT_ENVIRONMENT = false;
  });

  test('opens the default read-only timetable before even the first API response', async () => {
    await act(async () => root.render(<App />));
    expect(window.location.pathname).toBe('/timetable');
    expect(container.textContent).toContain('只读课表');
    expect(container.querySelector('.loading')).toBeNull();
    expect(getAccessStatus).toHaveBeenCalledTimes(1);
    expect(getClientBootstrap).not.toHaveBeenCalled();
  });

  test.each(['manual logout', 'missing cache', 'another page'])(
    'does not turn cache recovery into an authentication bypass: %s', async reason => {
      if (reason === 'manual logout') sessionStorage.setItem('neu_manual_logout', '1');
      if (reason === 'missing cache') localStorage.removeItem('neu-toolbox-timetable-recovery-namespace');
      if (reason === 'another page') window.history.replaceState({}, '', '/scores');
      await act(async () => root.render(<App />));
      expect(container.querySelector('[data-testid="timetable"]')).toBeNull();
      expect(container.querySelector('.loading')).not.toBeNull();
    },
  );
});
