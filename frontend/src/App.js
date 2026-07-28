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
import GradeTrackingPage from './pages/GradeTrackingPage';
import GradeTrackingRecoveryPage from './pages/GradeTrackingRecoveryPage';
import ResearchTrainingPage from './pages/ResearchTrainingPage';
import LogsPage from './pages/LogsPage';
import AccessLoginPage from './pages/AccessLoginPage';
import { checkStatus, getAccessStatus, getHealth, getOfflineStatus } from './services/api';
import './App.css';

const { Content } = Layout;
const OFFLINE_SESSION_KEY = 'neu_offline_mode';

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
  const recoveryMatch = window.location.pathname.match(
    /^\/grade-tracking\/recovery\/([^/]+)\/?$/
  );
  const recoveryToken = recoveryMatch
    ? decodeURIComponent(recoveryMatch[1])
    : null;
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);
  const [accessState, setAccessState] = useState({
    required: false,
    configured: true,
    authenticated: true,
  });
  const [runtimeProfile, setRuntimeProfile] = useState('development');
  const [offlineMode, setOfflineMode] = useState(
    () => sessionStorage.getItem(OFFLINE_SESSION_KEY) === '1'
  );
  const [offlineCapabilities, setOfflineCapabilities] = useState({
    has_scores: false,
    has_report: false,
  });

  const loadApplicationState = async () => {
    const [access, health] = await Promise.all([getAccessStatus(), getHealth()]);
    setAccessState(access);
    setRuntimeProfile(health.profile || 'development');
    if (!access.required || access.authenticated) {
      if (sessionStorage.getItem(OFFLINE_SESSION_KEY) === '1') {
        let offline = null;
        try {
          offline = await getOfflineStatus();
        } catch (error) {
          console.warn('恢复离线模式失败，将检查在线登录状态', error);
        }
        if (offline?.available) {
          setOfflineMode(true);
          setOfflineCapabilities(offline);
          setIsLoggedIn(true);
          setUserInfo(offline.username || '离线用户');
          return access;
        }
        sessionStorage.removeItem(OFFLINE_SESSION_KEY);
        setOfflineMode(false);
        setOfflineCapabilities({ has_scores: false, has_report: false });
        const status = await checkStatus();
        setIsLoggedIn(status.is_logged_in);
        setUserInfo(status.current_user);
      } else {
        const status = await checkStatus();
        setIsLoggedIn(status.is_logged_in);
        setUserInfo(status.current_user);
      }
    }
    return access;
  };

  // 先检查服务器访问门，再检查教务登录状态。
  useEffect(() => {
    const init = async () => {
      if (recoveryToken) {
        setIsLoading(false);
        return;
      }
      try {
        await loadApplicationState();
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
  }, [recoveryToken]);

  useEffect(() => {
    const requireAccess = () => {
      setAccessState(previous => ({ ...previous, required: true, authenticated: false }));
      setIsLoggedIn(false);
      setUserInfo(null);
    };
    window.addEventListener('neu-access-required', requireAccess);
    return () => window.removeEventListener('neu-access-required', requireAccess);
  }, []);

  useEffect(() => {
    const requireAuthentication = () => {
      if (offlineMode) return;
      setIsLoggedIn(false);
      setUserInfo(null);
      message.info('登录已失效，请重新完成 WebVPN 认证');
    };
    window.addEventListener('neu-auth-required', requireAuthentication);
    return () => window.removeEventListener('neu-auth-required', requireAuthentication);
  }, [offlineMode]);

  const handleLoginSuccess = (username) => {
    sessionStorage.removeItem(OFFLINE_SESSION_KEY);
    setOfflineMode(false);
    setOfflineCapabilities({ has_scores: false, has_report: false });
    setIsLoggedIn(true);
    setUserInfo(username);
    message.success('登录成功');
  };

  const handleLogout = () => {
    const wasOffline = offlineMode;
    sessionStorage.removeItem(OFFLINE_SESSION_KEY);
    setOfflineMode(false);
    setOfflineCapabilities({ has_scores: false, has_report: false });
    setIsLoggedIn(false);
    setUserInfo(null);
    message.success(wasOffline ? '已退出离线模式' : '已登出');
  };

  const handleOfflineSuccess = (status) => {
    sessionStorage.setItem(OFFLINE_SESSION_KEY, '1');
    setOfflineMode(true);
    setOfflineCapabilities(status);
    setIsLoggedIn(true);
    setUserInfo(status.username || '离线用户');
    message.success('已进入只读离线模式');
  };

  const offlineDefaultPath = offlineCapabilities.has_scores
    ? '/scores'
    : '/academic-report';

  if (isLoading) {
    return (
      <div className="loading" role="status" aria-live="polite">
        <Spin size="large" />
        <span>正在连接教务服务</span>
      </div>
    );
  }

  if (recoveryToken) {
    return (
      <ConfigProvider theme={appTheme}>
        <GradeTrackingRecoveryPage token={recoveryToken} />
      </ConfigProvider>
    );
  }

  if (accessState.required && !accessState.authenticated) {
    return (
      <ConfigProvider theme={appTheme}>
        <AccessLoginPage
          configured={accessState.configured}
          onSuccess={async () => {
            setIsLoading(true);
            try {
              await loadApplicationState();
            } finally {
              setIsLoading(false);
            }
          }}
        />
      </ConfigProvider>
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
                  <LoginPage
                    onLoginSuccess={handleLoginSuccess}
                    onOfflineSuccess={handleOfflineSuccess}
                  />
              } 
            />
            <Route 
              path="/" 
              element={
                isLoggedIn ? 
                  <MainLayout
                    userInfo={userInfo}
                    onLogout={handleLogout}
                    runtimeProfile={runtimeProfile}
                    offlineMode={offlineMode}
                    offlineCapabilities={offlineCapabilities}
                  /> :
                  <Navigate to="/login" />
              }
            >
              <Route index element={<Navigate to={offlineMode ? offlineDefaultPath : '/scores'} />} />
              <Route
                path="scores"
                element={!offlineMode || offlineCapabilities.has_scores
                  ? <ScoresPage offlineMode={offlineMode} />
                  : <Navigate to={offlineDefaultPath} />}
              />
              <Route path="grade-tracking" element={offlineMode ? <Navigate to={offlineDefaultPath} /> : <GradeTrackingPage />} />
              <Route
                path="academic-report"
                element={!offlineMode || offlineCapabilities.has_report
                  ? <AcademicReportPage offlineMode={offlineMode} />
                  : <Navigate to={offlineDefaultPath} />}
              />
              <Route path="experiment-courses" element={offlineMode ? <Navigate to={offlineDefaultPath} /> : <ExperimentCoursePage />} />
              <Route path="research-training" element={offlineMode ? <Navigate to={offlineDefaultPath} /> : <ResearchTrainingPage />} />
              <Route path="evaluation" element={offlineMode ? <Navigate to={offlineDefaultPath} /> : <EvaluationPage />} />
              <Route path="exams" element={offlineMode ? <Navigate to={offlineDefaultPath} /> : <ExamPage />} />
              <Route path="logs" element={offlineMode ? <Navigate to={offlineDefaultPath} /> : <LogsPage />} />
            </Route>
            </Routes>
          </Content>
        </Layout>
      </Router>
    </ConfigProvider>
  );
}

export default App;
