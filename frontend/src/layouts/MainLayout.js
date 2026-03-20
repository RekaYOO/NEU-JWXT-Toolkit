import React, { useState, useEffect } from 'react';
import { Layout, Menu, Button, Avatar, Dropdown, Space, message } from 'antd';
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
} from '@ant-design/icons';
import { logout, getUserInfo, getUserAvatar } from '../services/api';
import './MainLayout.css';

const { Header, Sider, Content } = Layout;

const AVATAR_STORAGE_KEY = 'neu_user_avatar';
const AVATAR_TIMESTAMP_KEY = 'neu_user_avatar_timestamp';

const MainLayout = ({ userInfo, onLogout }) => {
  const [collapsed, setCollapsed] = useState(false);
  const [avatarUrl, setAvatarUrl] = useState(null);
  const [isRefreshingAvatar, setIsRefreshingAvatar] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

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

    if (userInfo) {
      loadAvatar();
    }
  }, [userInfo]);

  // 刷新头像（点击头像时调用）
  const refreshAvatar = async () => {
    if (isRefreshingAvatar) return;
    
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
    try {
      await logout();
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
      label: '退出登录',
      onClick: handleLogout,
    },
  ];

  const menuItems = [
    {
      key: '/scores',
      icon: <BookOutlined />,
      label: '成绩',
    },
    {
      key: '/academic-report',
      icon: <ScheduleOutlined />,
      label: '培养计划',
    },
    {
      key: '/experiment-courses',
      icon: <ExperimentOutlined />,
      label: '实验选课',
    },
    {
      key: '/logs',
      icon: <FileTextOutlined />,
      label: '系统日志',
    },
  ];

  const onMenuClick = ({ key }) => {
    navigate(key);
  };

  return (
    <Layout className="main-layout">
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        className="main-sider"
      >
        <div className="logo">
          {collapsed ? 'NEU' : 'NEU工具箱'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={onMenuClick}
        />
      </Sider>

      <Layout>
        <Header className="main-header">
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            className="collapse-btn"
          />

          <div className="header-right">
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <Space className="user-info">
                <Avatar 
                  src={avatarUrl} 
                  icon={!avatarUrl && <UserOutlined />}
                  onClick={refreshAvatar}
                  style={{ cursor: 'pointer' }}
                  title="点击刷新头像"
                />
                <span className="username">{userInfo || '用户'}</span>
              </Space>
            </Dropdown>
          </div>
        </Header>

        <Content className="main-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default MainLayout;
