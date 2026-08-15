import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import { checkStatus, getAccessStatus, getHealth } from './services/api';

jest.mock('./services/api', () => ({
  checkStatus: jest.fn(),
  getAccessStatus: jest.fn(),
  getHealth: jest.fn(),
  getOfflineStatus: jest.fn(),
}));

test('keeps a deep link behind the auth loading gate while status recovery is pending', async () => {
  jest.useFakeTimers();
  sessionStorage.clear();
  window.history.replaceState({}, '', '/course-selection/BATCH-1/catalog');
  getAccessStatus.mockResolvedValue({ required: false, configured: true, authenticated: true });
  getHealth.mockResolvedValue({ profile: 'development' });
  checkStatus.mockReturnValue(new Promise(() => {}));

  const previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(<App />);
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(3000);
    });

    expect(window.location.pathname).toBe('/course-selection/BATCH-1/catalog');
    expect(container.textContent).toContain('正在恢复登录状态，请稍候');
    expect(container.querySelector('.login-page')).toBeNull();
  } finally {
    await act(async () => root.unmount());
    container.remove();
    global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
    jest.useRealTimers();
  }
});
