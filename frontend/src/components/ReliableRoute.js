import React, { useEffect, useState } from 'react';
import { Alert, Button, Skeleton } from 'antd';

import {
  loadRouteComponent,
  loadedRouteComponent,
  retryRouteComponent,
} from '../utils/routeModules';
import { isChunkLoadError, recoverFromStaleBuild } from '../utils/routeLoadRecovery';

const wait = duration => new Promise(resolve => window.setTimeout(resolve, duration));

class RouteRenderBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) { return { error }; }

  componentDidUpdate(previous) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error) {
    console.error('业务页面渲染失败', error);
  }

  render() {
    if (this.state.error) {
      return <div className="route-load-state"><Alert type="error" showIcon message="页面渲染失败" description="页面代码已加载，但渲染时发生错误。请重试；若仍失败，可刷新应用。" action={<Button onClick={this.props.onRetry}>重试页面</Button>} /></div>;
    }
    return this.props.children;
  }
}

export default function ReliableRoute({ routeId, componentProps = {} }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState(() => ({
    Component: loadedRouteComponent(routeId),
    error: null,
    loading: !loadedRouteComponent(routeId),
  }));

  useEffect(() => {
    let active = true;
    const run = async () => {
      const cached = loadedRouteComponent(routeId);
      if (cached) {
        setState({ Component: cached, error: null, loading: false });
        return;
      }
      setState({ Component: null, error: null, loading: true });
      try {
        const Component = await loadRouteComponent(routeId);
        if (active) setState({ Component, error: null, loading: false });
      } catch (firstError) {
        if (!active) return;
        if (await recoverFromStaleBuild({ error: firstError })) return;
        if (isChunkLoadError(firstError)) {
          try {
            await wait(400);
            const Component = await retryRouteComponent(routeId);
            if (active) setState({ Component, error: null, loading: false });
            return;
          } catch (retryError) {
            if (!active) return;
            if (await recoverFromStaleBuild({ error: retryError })) return;
            setState({ Component: null, error: retryError, loading: false });
            return;
          }
        }
        setState({ Component: null, error: firstError, loading: false });
      }
    };
    run();
    return () => { active = false; };
  }, [attempt, routeId]);

  const retry = () => setAttempt(value => value + 1);
  if (state.loading) {
    return <div className="route-load-state" role="status" aria-live="polite"><Skeleton active paragraph={{ rows: 6 }} /><span>正在加载页面资源</span></div>;
  }
  if (state.error) {
    const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
    return <div className="route-load-state"><Alert type="error" showIcon message={offline ? '页面资源尚未下载' : '页面资源加载失败'} description={offline ? '当前设备处于离线状态。联网后可重试加载，现有登录和缓存状态不会改变。' : '应用外壳仍可使用。请重试加载；若刚完成版本更新，可刷新应用读取最新资源。'} action={<div className="route-load-actions"><Button onClick={retry}>重试加载</Button><Button onClick={() => window.location.reload()}>刷新应用</Button></div>} /></div>;
  }
  const Component = state.Component;
  return <RouteRenderBoundary resetKey={`${routeId}:${attempt}`} onRetry={retry}><Component {...componentProps} /></RouteRenderBoundary>;
}
