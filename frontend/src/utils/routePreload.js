import { preloadRoute, routeIdForPath } from './routeModules';

export const localCodeRuntime = ({ runtimeProfile, locationRef = window.location } = {}) => (
  runtimeProfile === 'desktop'
  || runtimeProfile === 'mobile'
  || ['localhost', '127.0.0.1', '::1'].includes(locationRef.hostname)
);

export const shouldPreloadAllRoutes = ({ runtimeProfile, connection = navigator.connection, locationRef } = {}) => {
  if (localCodeRuntime({ runtimeProfile, locationRef })) return true;
  if (connection?.saveData) return false;
  return !/(^|-)2g$/.test(connection?.effectiveType || '');
};

export const scheduleRoutePreloads = ({
  paths,
  runtimeProfile,
  documentRef = document,
  windowRef = window,
  connection = navigator.connection,
  preload = preloadRoute,
} = {}) => {
  if (!shouldPreloadAllRoutes({ runtimeProfile, connection, locationRef: windowRef.location })) return () => {};
  const queue = [...new Set((paths || []).map(routeIdForPath).filter(Boolean))];
  let cancelled = false;
  let scheduled = null;
  const cancelScheduled = () => {
    if (scheduled == null) return;
    if (windowRef.cancelIdleCallback) windowRef.cancelIdleCallback(scheduled);
    else windowRef.clearTimeout(scheduled);
    scheduled = null;
  };
  const schedule = () => {
    if (cancelled || scheduled != null || !queue.length || documentRef.hidden) return;
    const run = async () => {
      scheduled = null;
      if (cancelled || documentRef.hidden) return;
      try {
        await preload(queue.shift());
      } catch (_error) {
        // 后台代码预取失败不打扰当前页面，也不能阻塞后续可访问页面。
      }
      schedule();
    };
    scheduled = windowRef.requestIdleCallback
      ? windowRef.requestIdleCallback(run, { timeout: 2000 })
      : windowRef.setTimeout(run, 1200);
  };
  const visibility = () => { if (documentRef.hidden) cancelScheduled(); else schedule(); };
  documentRef.addEventListener('visibilitychange', visibility);
  schedule();
  return () => {
    cancelled = true;
    cancelScheduled();
    documentRef.removeEventListener('visibilitychange', visibility);
  };
};
