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
  const navigate = useNavigate();
  const location = useLocation();

  // 加载用户头像
  useEffect(() => {
    const loadAvatar = async () => {
      // 先尝试从本地加载
      const cachedAvatar = localStorage.getItem(AVATAR_STORAGE_KEY);
      const cachedTimestamp = localStorage.getItem(AVATAR_TIMESTAMP_KEY);
      
      // 如果缓存存在且在7天内，使用缓存
      if (cachedAvatar && cachedTimestamp) {
        const age = Date.now() - parseInt(cachedTimestamp);
        if (age < 7 * 24 * 60 * 60 * 1000) {
          setAvatarUrl(cachedAvatar);
        }
      }

      // 从服务器获取最新头像
      try {
        const userInfoData = await getUserInfo();
        // 如果没有头像URL，不报错，直接返回
        if (!userInfoData?.avatar_token) {
          console.log('[Avatar] 用户未上传头像');
          return;
        }
        
        try {
          const avatarBlob = await getUserAvatar();
          if (avatarBlob && avatarBlob.size > 0) {
            const url = URL.createObjectURL(avatarBlob);
            setAvatarUrl(url);
            
            // 转换为 base64 存储到 localStorage
            const reader = new FileReader();
            reader.onloadend = () => {
              localStorage.setItem(AVATAR_STORAGE_KEY, reader.result);
              localStorage.setItem(AVATAR_TIMESTAMP_KEY, Date.now().toString());
            };
            reader.readAsDataURL(avatarBlob);
          }
        } catch (avatarError) {
          // 头像获取失败不显示错误，静默处理
          console.log('[Avatar] 头像获取失败，使用默认头像');
        }
      } catch (error) {
        console.log('[Avatar] 获取用户信息失败:', error);
      }
    };

    if (userInfo) {
      loadAvatar();
    }
  }, [userInfo]);

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
