import {
  clearManualLogout,
  isManualLogoutActive,
  markManualLogout,
} from './authSessionPolicy';

test('主动退出标记只在明确清除后恢复自动登录', () => {
  sessionStorage.clear();
  expect(isManualLogoutActive()).toBe(false);

  markManualLogout();
  expect(isManualLogoutActive()).toBe(true);

  clearManualLogout();
  expect(isManualLogoutActive()).toBe(false);
});
