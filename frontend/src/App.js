import React, { useState, useEffect } from 'react';
import { Layout, message } from 'antd';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import MainLayout from './layouts/MainLayout';
import ScoresPage from './pages/ScoresPage';
import AcademicReportPage from './pages/AcademicReportPage';
import ExperimentCoursePage from './pages/ExperimentCoursePage';
import LogsPage from './pages/LogsPage';
import { checkStatus } from './services/api';
import './App.css';

const { Content } = Layout;

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
    return <div className="loading">加载中...</div>;
  }

  return (
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
              <Route path="logs" element={<LogsPage />} />
            </Route>
          </Routes>
        </Content>
      </Layout>
    </Router>
  );
}

export default App;
