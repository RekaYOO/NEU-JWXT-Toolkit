import React, { useState, useEffect } from 'react';
import { ConfigProvider, Layout, Spin, message } from 'antd';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import MainLayout from './layouts/MainLayout';
import ScoresPage from './pages/ScoresPage';
import AcademicReportPage from './pages/AcademicReportPage';
import ExperimentCoursePage from './pages/ExperimentCoursePage';
import EvaluationPage from './pages/EvaluationPage';
import ExamPage from './pages/ExamPage';
import LogsPage from './pages/LogsPage';
import { checkStatus } from './services/api';
import './App.css';

const { Content } = Layout;

const appTheme = {
  token: {
    colorPrimary: '#2563eb',
    colorInfo: '#2563eb',
    colorSuccess: '#16a34a',
    colorWarning: '#d97706',
    colorError: '#dc2626',
    colorText: '#1e293b',
    colorTextSecondary: '#64748b',
    colorBorder: '#d8e0e8',
    colorBorderSecondary: '#e8edf2',
    colorBgLayout: '#f4f6f8',
    colorBgContainer: '#ffffff',
    borderRadius: 6,
    borderRadiusLG: 8,
    controlHeight: 36,
    fontFamily: "'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif",
  },
  components: {
    Button: {
      borderRadius: 6,
      primaryShadow: 'none',
      defaultShadow: 'none',
      fontWeight: 600,
    },
    Card: {
      borderRadiusLG: 8,
      headerBg: '#ffffff',
      paddingLG: 20,
    },
    Menu: {
      darkItemBg: '#ffffff',
      darkSubMenuItemBg: '#ffffff',
      darkItemColor: '#475569',
      darkItemHoverBg: '#f1f5f9',
      darkItemSelectedBg: '#eaf2ff',
      darkItemSelectedColor: '#1d4ed8',
      itemBorderRadius: 6,
    },
    Table: {
      headerBg: '#f4f6f7',
      headerColor: '#34414b',
      headerSplitColor: '#e1e6e9',
      rowHoverBg: '#f5fafb',
      borderColor: '#e1e6e9',
    },
    Tabs: {
      itemSelectedColor: '#2563eb',
      inkBarColor: '#2563eb',
    },
  },
};

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);

  // 检查登录状态（带超时处理）
  useEffect(() => {
    const init = async () => {
      try {
        const status = await checkStatus();
        setIsLoggedIn(status.is_logged_in);
        setUserInfo(status.current_user);
      } catch (error) {
        // 静默处理，不弹窗打扰用户，只在控制台记录
        console.log('后端服务未就绪，以未登录状态启动');
      } finally {
        setIsLoading(false);
      }
    };
    // 即使请求卡住，最多等3秒就强制显示页面
    const timer = setTimeout(() => {
      setIsLoading(false);
    }, 3000);
    
    init();
    
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    const requireAuthentication = () => {
      setIsLoggedIn(false);
      setUserInfo(null);
      message.info('登录已失效，请重新完成 WebVPN 认证');
    };
    window.addEventListener('neu-auth-required', requireAuthentication);
    return () => window.removeEventListener('neu-auth-required', requireAuthentication);
  }, []);

  const handleLoginSuccess = (username) => {
    setIsLoggedIn(true);
    setUserInfo(username);
    message.success('登录成功');
  };

  const handleLogout = () => {
    setIsLoggedIn(false);
    setUserInfo(null);
    message.success('已登出');
  };

  if (isLoading) {
    return (
      <div className="loading" role="status" aria-live="polite">
        <div className="loading-mark">NEU</div>
        <Spin size="large" />
        <span>正在连接教务服务</span>
      </div>
    );
  }

  return (
    <ConfigProvider theme={appTheme}>
      <Router>
        <Layout className="app-layout">
          <Content className="app-content">
            <Routes>
            <Route 
              path="/login" 
              element={
                isLoggedIn ? 
                  <Navigate to="/" /> : 
                  <LoginPage onLoginSuccess={handleLoginSuccess} />
              } 
            />
            <Route 
              path="/" 
              element={
                isLoggedIn ? 
                  <MainLayout userInfo={userInfo} onLogout={handleLogout} /> :
                  <Navigate to="/login" />
              }
            >
              <Route index element={<Navigate to="/scores" />} />
              <Route path="scores" element={<ScoresPage />} />
              <Route path="academic-report" element={<AcademicReportPage />} />
              <Route path="experiment-courses" element={<ExperimentCoursePage />} />
              <Route path="evaluation" element={<EvaluationPage />} />
              <Route path="exams" element={<ExamPage />} />
              <Route path="logs" element={<LogsPage />} />
            </Route>
            </Routes>
          </Content>
        </Layout>
      </Router>
    </ConfigProvider>
  );
}

export default App;
