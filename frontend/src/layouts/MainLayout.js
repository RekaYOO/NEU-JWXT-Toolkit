import React, { useState, useEffect } from 'react';
import { Layout, Menu, Button, Avatar, Drawer, Dropdown, Grid, Tooltip, message, Modal } from 'antd';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  BookOutlined,
  UserOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  FileTextOutlined,
  ScheduleOutlined,
  ExperimentOutlined,
  StarOutlined,
  CalendarOutlined,
  MenuOutlined,
  PoweroffOutlined,
  BellOutlined,
  ReadOutlined,
  ExportOutlined,
} from '@ant-design/icons';
import { logout, getUserAvatar, shutdownRuntime } from '../services/api';
import './MainLayout.css';

const { Header, Sider, Content } = Layout;

const AVATAR_STORAGE_KEY = 'neu_user_avatar';
const AVATAR_TIMESTAMP_KEY = 'neu_user_avatar_timestamp';

const menuItems = [
  { key: '/scores', icon: <BookOutlined />, label: '成绩明细' },
  { key: '/grade-tracking', icon: <BellOutlined />, label: '成绩追踪' },
  { key: '/academic-report', icon: <ScheduleOutlined />, label: '培养计划' },
  { key: '/experiment-courses', icon: <ExperimentOutlined />, label: '实验选课' },
  { key: '/research-training', icon: <ReadOutlined />, label: '科研训练' },
  { key: '/evaluation', icon: <StarOutlined />, label: '自动评教' },
  { key: '/exams', icon: <CalendarOutlined />, label: '我的考试' },
  { key: '/export', icon: <ExportOutlined />, label: '导出下载' },
  { key: '/logs', icon: <FileTextOutlined />, label: '系统日志' },
];

const pageTitles = Object.fromEntries(menuItems.map(({ key, label }) => [key, label]));

const MainLayout = ({
  userInfo,
  onLogout,
  runtimeProfile = 'development',
  offlineMode = false,
  offlineCapabilities = {},
}) => {
  const [collapsed, setCollapsed] = useState(true);
  const [avatarUrl, setAvatarUrl] = useState(null);
  const [isRefreshingAvatar, setIsRefreshingAvatar] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [serviceStopped, setServiceStopped] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const visibleMenuItems = offlineMode
    ? menuItems.filter(item => (
      (item.key === '/scores' && offlineCapabilities.has_scores)
      || (item.key === '/academic-report' && offlineCapabilities.has_report)
      || (item.key === '/research-training' && offlineCapabilities.has_research)
      || item.key === '/export'
    ))
    : menuItems;

  // 加载用户头像（仅使用缓存，不自动下载）
  useEffect(() => {
    const loadAvatar = async () => {
      try {
        // 从服务器获取头像（会自动使用缓存）
        const avatarBlob = await getUserAvatar(false);
        if (avatarBlob && avatarBlob.size > 0) {
          const url = URL.createObjectURL(avatarBlob);
          setAvatarUrl(url);
        }
      } catch (error) {
        // 头像获取失败不显示错误，使用默认头像
        console.log('[Avatar] 使用默认头像');
      }
    };

    if (userInfo && !offlineMode) {
      loadAvatar();
    }
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
        const avatarBlob = await getUserAvatar(false);
        if (avatarBlob && avatarBlob.size > 0) {
          setAvatarUrl(URL.createObjectURL(avatarBlob));
        }
      } catch (error) {
        // SWR keeps the previous avatar when a background refresh fails.
      }
    };
    window.addEventListener('neu-cache-event', onCacheEvent);
    return () => window.removeEventListener('neu-cache-event', onCacheEvent);
  }, [offlineMode]);

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
    try {
      const result = await logout();
      if (!result.success) {
        throw new Error(result.message || '后端未完成登出');
      }
      // 清除头像缓存
      localStorage.removeItem(AVATAR_STORAGE_KEY);
      localStorage.removeItem(AVATAR_TIMESTAMP_KEY);
      if (avatarUrl && avatarUrl.startsWith('blob:')) {
        URL.revokeObjectURL(avatarUrl);
      }
      onLogout();
      navigate('/login');
    } catch (error) {
      message.error('登出失败');
    }
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
      label: '退出本地服务',
      onClick: () => {
        Modal.confirm({
          title: '退出本地服务？',
          content: '退出后需要从桌面或开始菜单重新启动。',
          okText: '退出',
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
              message.error('退出本地服务失败，请稍后重试');
              throw error;
            }
          },
        });
      },
    }] : []),
  ];

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
      items={visibleMenuItems}
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
        <Header className={`main-header ${location.pathname === '/academic-report' ? 'has-center-slot' : ''}`}>
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
