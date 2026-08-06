export const MANUAL_LOGOUT_SESSION_KEY = 'neu_manual_logout';

export const isManualLogoutActive = () => (
  sessionStorage.getItem(MANUAL_LOGOUT_SESSION_KEY) === '1'
);

export const markManualLogout = () => {
  sessionStorage.setItem(MANUAL_LOGOUT_SESSION_KEY, '1');
};

export const clearManualLogout = () => {
  sessionStorage.removeItem(MANUAL_LOGOUT_SESSION_KEY);
};
