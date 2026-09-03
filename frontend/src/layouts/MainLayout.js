import React, { useRef, useState, useEffect } from 'react';
import { Layout, Menu, Button, Avatar, Drawer, Dropdown, Grid, Tooltip, message, Modal } from 'antd';
import { Outlet, useNavigate, useLocation, Navigate } from 'react-router-dom';
import {
  UserOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MenuOutlined,
  PoweroffOutlined,
} from '@ant-design/icons';
import {
  logout,
  getUserAvatar,
  getUserAvatarCache,
  requestCacheRefresh,
  shutdownRuntime,
  waitForCacheRefreshJob,
} from '../services/api';
import {
  readBrowserAvatarCache,
  subscribeBrowserAvatarCache,
  writeBrowserAvatarCache,
} from '../resources/BrowserTimetableStore';
import { pageTitles, visibleMenuItems } from '../features/featureRegistry';
import './MainLayout.css';

const { Header, Sider, Content } = Layout;

const AVATAR_STORAGE_KEY = 'neu_user_avatar';
const AVATAR_TIMESTAMP_KEY = 'neu_user_avatar_timestamp';

const MainLayout = ({
  userInfo,
  onLogout,
  runtimeProfile = 'development',
  offlineMode = false,
  offlineCapabilities = {},
  recoveryMode = false,
}) => {
  const [collapsed, setCollapsed] = useState(true);
  const [avatarUrl, setAvatarUrl] = useState(null);
  const [isRefreshingAvatar, setIsRefreshingAvatar] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [serviceStopped, setServiceStopped] = useState(false);
  const avatarGeneration = useRef(0);
  const navigate = useNavigate();
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const menuItems = visibleMenuItems({ offlineMode, offlineCapabilities });

  // 首屏先读浏览器头像，再读取服务器 cache-only 快照；过期刷新由统一协调器处理。
  useEffect(() => {
    const generation = ++avatarGeneration.current;
    let active = true;
    const show = blob => {
      if (!blob || blob.size <= 0) return;
      setAvatarUrl(previous => {
        if (previous && previous.startsWith('blob:')) URL.revokeObjectURL(previous);
        return URL.createObjectURL(blob);
      });
    };
    const reloadAfterRefresh = async refresh => {
      const jobId = refresh?.job_id || refresh?.id;
      if (refresh?.status === 'fresh') return getUserAvatarCache();
      if (!jobId || !['started', 'running'].includes(String(refresh?.status || ''))) {
        return null;
      }
      try {
        const job = await waitForCacheRefreshJob(jobId, {
          intervalMs: 750,
          timeoutMs: 30000,
        });
        if (!active || generation !== avatarGeneration.current) return null;
        return job?.status === 'completed' ? getUserAvatarCache() : null;
      } catch (_error) {
        return null;
      }
    };
    const loadAvatar = async () => {
      const identity = String(userInfo || '');
      try {
        const browser = await readBrowserAvatarCache(identity);
        if (active && generation === avatarGeneration.current && browser?.blob) show(browser.blob);
      } catch (_error) {
        // IndexedDB is optional; continue to the server cache.
      }

      let cached = null;
      try {
        cached = await getUserAvatarCache();
      } catch (_error) {
        // A server/cache/auth failure must not remove an already displayed
        // browser snapshot or prevent the page from rendering.
      }
      if (!active || generation !== avatarGeneration.current) return;
      if (cached?.blob) {
        show(cached.blob);
        await writeBrowserAvatarCache(identity, cached);
      }

      if (cached?.stale || !cached?.blob) {
        try {
          const refresh = await requestCacheRefresh('avatar', { reason: 'page_swr' });
          const refreshed = await reloadAfterRefresh(refresh);
          if (
            active
            && generation === avatarGeneration.current
            && refreshed?.blob
          ) {
            show(refreshed.blob);
            await writeBrowserAvatarCache(identity, refreshed);
          }
        } catch (_error) {
          // SWR failures are non-fatal; retain the browser/server snapshot.
        }
      }
    };
    if (userInfo && !offlineMode) loadAvatar();
    const unsubscribe = userInfo && !offlineMode
      ? subscribeBrowserAvatarCache(String(userInfo), async () => {
        try {
          const cached = await getUserAvatarCache();
          if (active && generation === avatarGeneration.current && cached?.blob) {
            show(cached.blob);
            await writeBrowserAvatarCache(String(userInfo), cached);
          }
        } catch (_error) { /* retain the last good avatar */ }
      })
      : () => {};
    return () => {
      active = false;
      unsubscribe();
    };
  }, [userInfo, offlineMode]);

  useEffect(() => () => {
    if (avatarUrl && avatarUrl.startsWith('blob:')) {
      URL.revokeObjectURL(avatarUrl);
    }
  }, [avatarUrl]);

  useEffect(() => {
    if (offlineMode) return undefined;
    const onCacheEvent = async (event) => {
      const update = event.detail || {};
      if (update.resource !== 'avatar' || update.changed !== true) return;
      try {
        const cached = await getUserAvatarCache();
        if (cached?.blob) {
          setAvatarUrl(previous => {
            if (previous && previous.startsWith('blob:')) URL.revokeObjectURL(previous);
            return URL.createObjectURL(cached.blob);
          });
          await writeBrowserAvatarCache(String(userInfo || ''), cached);
        }
      } catch (error) {
        // SWR keeps the previous avatar when a background refresh fails.
      }
    };
    window.addEventListener('neu-cache-event', onCacheEvent);
    return () => window.removeEventListener('neu-cache-event', onCacheEvent);
  }, [offlineMode, userInfo]);

  // 刷新头像（点击头像时调用）
  const refreshAvatar = async () => {
    if (offlineMode || isRefreshingAvatar) return;
    
    setIsRefreshingAvatar(true);
    try {
      const avatarBlob = await getUserAvatar(true);
      if (avatarBlob && avatarBlob.size > 0) {
        // 释放旧的 blob URL
        if (avatarUrl && avatarUrl.startsWith('blob:')) {
          URL.revokeObjectURL(avatarUrl);
        }
        const url = URL.createObjectURL(avatarBlob);
        setAvatarUrl(url);
        try {
          const cached = await getUserAvatarCache();
          if (cached?.blob) await writeBrowserAvatarCache(String(userInfo || ''), cached);
        } catch (_error) { /* freshly displayed avatar remains usable */ }
        message.success('头像已更新');
      }
    } catch (error) {
      message.error('头像更新失败');
    } finally {
      setIsRefreshingAvatar(false);
    }
  };

  const handleLogout = async () => {
    if (offlineMode) {
      onLogout();
      navigate('/login');
      return;
    }
    // 先完成本地退出和导航，避免服务端会话锁、网络超时或已失效
    // 的远端 Session 把用户卡在工作台。服务端清理在后台尽力执行。
    const clearLocalSession = () => {
      localStorage.removeItem(AVATAR_STORAGE_KEY);
      localStorage.removeItem(AVATAR_TIMESTAMP_KEY);
      if (avatarUrl && avatarUrl.startsWith('blob:')) {
        URL.revokeObjectURL(avatarUrl);
      }
      onLogout();
      navigate('/login');
    };
    clearLocalSession();
    try {
      const result = await logout();
      if (!result.success) {
        throw new Error(result.message || '后端未完成登出');
      }
    } catch (error) {
      message.warning('本地已退出登录，服务器会话清理将在下次连接时完成');
    }
  };

  const confirmShutdown = () => {
    Modal.confirm({
      title: '退出桌面程序？',
      content: '退出后本地服务会停止。如需再次使用，请重新运行 NEU 教务工具箱。',
      okText: '退出程序',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await shutdownRuntime();
          if (!result.success) {
            throw new Error('本地服务未确认退出');
          }
          setServiceStopped(true);
        } catch (error) {
          message.error('退出桌面程序失败，请稍后重试');
          throw error;
        }
      },
    });
  };

  const userMenuItems = [
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: offlineMode ? '退出离线模式' : '退出登录',
      onClick: handleLogout,
    },
    ...(runtimeProfile === 'desktop' ? [{
      type: 'divider',
    }, {
      key: 'shutdown',
      danger: true,
      icon: <PoweroffOutlined />,
      label: '退出桌面程序',
      onClick: confirmShutdown,
    }] : []),
  ];

  // 课表恢复态只允许查看本机快照。认证完成前即使用户手动输入其他
  // 路径，也不能借此打开任何需要在线身份的页面。
  if (
    recoveryMode
    && location.pathname !== '/'
    && !location.pathname.startsWith('/timetable')
  ) {
    return <Navigate to="/login" replace />;
  }

  if (serviceStopped) {
    return (
      <main className="service-stopped-page" role="status" aria-live="assertive">
        <div className="service-stopped-card">
          <span className="service-stopped-icon" aria-hidden="true">
            <PoweroffOutlined />
          </span>
          <span className="service-stopped-kicker">LOCAL SERVICE STOPPED</span>
          <h1>本地服务已退出</h1>
          <p>当前页面已无法继续使用，可以安全关闭。</p>
          <div className="service-stopped-hint">
            如需再次使用，请从桌面或开始菜单重新启动 NEU 教务工具箱。
          </div>
        </div>
      </main>
    );
  }

  const onMenuClick = ({ key }) => {
    navigate(key);
    setMobileNavOpen(false);
  };

  const handleNavigationToggle = () => {
    if (isMobile) {
      setMobileNavOpen((open) => !open);
      return;
    }
    setCollapsed((value) => !value);
  };

  const navigation = (
    <Menu
      theme="dark"
      mode="inline"
      selectedKeys={[location.pathname.startsWith('/export') ? '/export' : location.pathname]}
      items={menuItems}
      onClick={onMenuClick}
      aria-label="主要导航"
    />
  );

  const brand = (compact = false) => (
    <div className={`app-brand${compact ? ' is-compact' : ''}`}>
      <span className="app-brand-mark">NEU</span>
      {!compact && (
        <span className="app-brand-name">
          教务工具箱
          <small>ACADEMIC TOOLKIT</small>
        </span>
      )}
    </div>
  );

  const navigationToggle = (
    <Button
      type="text"
      aria-label={isMobile ? '打开导航' : (collapsed ? '展开导航' : '收起导航')}
      aria-expanded={isMobile ? mobileNavOpen : !collapsed}
      icon={isMobile ? <MenuOutlined /> : (collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />)}
      onClick={handleNavigationToggle}
      className="collapse-btn"
    />
  );

  return (
    <Layout className="main-layout">
      {!isMobile && (
        <Sider
          trigger={null}
          collapsible
          collapsed={collapsed}
          width={240}
          collapsedWidth={80}
          className="main-sider"
          onMouseEnter={() => setCollapsed(false)}
          onMouseMove={() => setCollapsed(false)}
          onPointerEnter={() => setCollapsed(false)}
          onMouseLeave={() => setCollapsed(true)}
          onPointerLeave={() => setCollapsed(true)}
        >
          {brand(collapsed)}
          <nav className="main-nav">{navigation}</nav>
          {!collapsed && (
            <div className="sider-footer">
              {offlineMode
                ? '只读离线模式 · 不连接教务系统'
                : runtimeProfile === 'server'
                ? '私有服务 · 仅限本人设备访问'
                : '本地运行 · 数据仅存于当前设备'}
            </div>
          )}
        </Sider>
      )}

      <Drawer
        className="mobile-nav-drawer"
        placement="left"
        width={280}
        open={isMobile && mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        title={brand(false)}
        styles={{ body: { padding: 0 } }}
      >
        <nav className="main-nav">{navigation}</nav>
      </Drawer>

      <Layout
        onMouseEnter={() => {
          if (!isMobile) setCollapsed(true);
        }}
        onMouseMove={() => {
          if (!isMobile) setCollapsed(true);
        }}
      >
        <Header className={`main-header ${location.pathname === '/academic-report' || location.pathname.startsWith('/timetable') ? 'has-center-slot' : ''}`}>
          <div className="header-leading">
            {isMobile ? navigationToggle : (
              <Tooltip title={collapsed ? '展开导航' : '收起导航'}>
                {navigationToggle}
              </Tooltip>
            )}
            <div className="page-context">
              <span className="page-context-label">教务工作台</span>
              <strong>{location.pathname.startsWith('/export') ? '导出下载' : (pageTitles[location.pathname] || '教务工具箱')}</strong>
            </div>
          </div>

          <div
            id="workspace-header-center"
            className="header-center-slot"
            aria-label="页面快捷信息"
          />

          <div className="header-right">
            {runtimeProfile === 'desktop' && (
              <Tooltip title="退出桌面程序">
                <Button
                  type="text"
                  danger
                  className="desktop-shutdown-btn"
                  aria-label="退出桌面程序"
                  icon={<PoweroffOutlined />}
                  onClick={confirmShutdown}
                />
              </Tooltip>
            )}
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight" trigger={['click']}>
              <Button type="text" className="user-info" aria-label="打开用户菜单">
                <Avatar 
                  src={avatarUrl} 
                  icon={!avatarUrl && <UserOutlined />}
                  onClick={offlineMode ? undefined : refreshAvatar}
                  title={offlineMode ? '离线模式不加载头像' : '点击刷新头像'}
                />
                {!isMobile && <span className="username">{userInfo || '用户'}</span>}
              </Button>
            </Dropdown>
          </div>
        </Header>

        <Content className="workspace-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default MainLayout;
