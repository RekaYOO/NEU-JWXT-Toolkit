import React, { useState, useEffect, useRef } from 'react';
import { ConfigProvider, Layout, Modal, Spin, message } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
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
import ExportPage from './pages/ExportPage';
import FestivalActivitiesPage from './pages/FestivalActivitiesPage';
import TimetablePage from './pages/TimetablePage';
import CourseOutlinePage from './pages/CourseOutlinePage';
import AcademicDocumentsPage from './pages/AcademicDocumentsPage';
import CourseSelectionPage from './pages/CourseSelectionPage';
import AccessLoginPage from './pages/AccessLoginPage';
import { checkStatus, getAccessStatus, getHealth, getOfflineStatus } from './services/api';
import { ResourceProvider } from './resources/ResourceStore';
import { isExportToolAvailable } from './export/exportTools';
import {
  featureAvailable,
  offlineDefaultPath as resolveOfflineDefaultPath,
} from './features/featureRegistry';
import {
  clearManualLogout,
  isManualLogoutActive,
  markManualLogout,
} from './utils/authSessionPolicy';
import './App.css';

const { Content } = Layout;
dayjs.locale('zh-cn');
const OFFLINE_SESSION_KEY = 'neu_offline_mode';
const EMPTY_OFFLINE_CAPABILITIES = {
  has_scores: false,
  has_report: false,
  has_research: false,
  resources: [],
  has_festival_activities: false,
};

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
  const [offlineCapabilities, setOfflineCapabilities] = useState(
    EMPTY_OFFLINE_CAPABILITIES
  );
  const authRecoveryPromptRef = useRef(false);
  const authRecoveryModalRef = useRef(null);

  const loadApplicationState = async () => {
    const [access, health] = await Promise.all([getAccessStatus(), getHealth()]);
    setAccessState(access);
    setRuntimeProfile(health.profile || 'development');
    if (!access.required || access.authenticated) {
      if (isManualLogoutActive()) {
        sessionStorage.removeItem(OFFLINE_SESSION_KEY);
        setOfflineMode(false);
        setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
        setIsLoggedIn(false);
        setUserInfo(null);
        return access;
      }
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
        setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
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
      authRecoveryModalRef.current?.destroy();
      authRecoveryModalRef.current = null;
      authRecoveryPromptRef.current = false;
      setAccessState(previous => ({ ...previous, required: true, authenticated: false }));
      setIsLoggedIn(false);
      setUserInfo(null);
    };
    window.addEventListener('neu-access-required', requireAccess);
    return () => window.removeEventListener('neu-access-required', requireAccess);
  }, []);

  useEffect(() => {
    const finishAsLoggedOut = () => {
      sessionStorage.removeItem(OFFLINE_SESSION_KEY);
      setOfflineMode(false);
      setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
      setIsLoggedIn(false);
      setUserInfo(null);
    };

    const requireAuthentication = async () => {
      if (
        offlineMode
        || isManualLogoutActive()
        || authRecoveryPromptRef.current
      ) return;
      authRecoveryPromptRef.current = true;

      let localStatus = null;
      try {
        localStatus = await getOfflineStatus();
      } catch (error) {
        console.warn('读取离线能力失败', error);
      }

      if (!localStatus?.available) {
        authRecoveryPromptRef.current = false;
        finishAsLoggedOut();
        message.warning('教务会话已失效，自动恢复未成功，请重新登录');
        return;
      }

      authRecoveryModalRef.current = Modal.confirm({
        title: '教务会话已失效',
        content: '系统已尝试静默恢复登录但未成功。你可以进入只读离线模式继续查看本地数据，或返回登录页重新认证。',
        okText: '进入离线模式',
        cancelText: '重新登录',
        onOk: () => {
          sessionStorage.setItem(OFFLINE_SESSION_KEY, '1');
          setOfflineMode(true);
          setOfflineCapabilities(localStatus);
          setIsLoggedIn(true);
          setUserInfo(localStatus.username || '离线用户');
          authRecoveryPromptRef.current = false;
          authRecoveryModalRef.current = null;
        },
        onCancel: () => {
          finishAsLoggedOut();
          authRecoveryPromptRef.current = false;
          authRecoveryModalRef.current = null;
        },
      });
    };
    window.addEventListener('neu-auth-required', requireAuthentication);
    return () => {
      window.removeEventListener('neu-auth-required', requireAuthentication);
      authRecoveryModalRef.current?.destroy();
      authRecoveryModalRef.current = null;
      authRecoveryPromptRef.current = false;
    };
  }, [offlineMode]);

  const handleLoginSuccess = (username) => {
    clearManualLogout();
    sessionStorage.removeItem(OFFLINE_SESSION_KEY);
    setOfflineMode(false);
    setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
    setIsLoggedIn(true);
    setUserInfo(username);
    message.success('登录成功');
  };

  const handleLogout = () => {
    const wasOffline = offlineMode;
    markManualLogout();
    sessionStorage.removeItem(OFFLINE_SESSION_KEY);
    setOfflineMode(false);
    setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
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

  const offlineDefaultPath = resolveOfflineDefaultPath(offlineCapabilities);

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
      <ConfigProvider theme={appTheme} locale={zhCN}>
        <GradeTrackingRecoveryPage token={recoveryToken} />
      </ConfigProvider>
    );
  }

  if (accessState.required && !accessState.authenticated) {
    return (
      <ConfigProvider theme={appTheme} locale={zhCN}>
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
    <ConfigProvider theme={appTheme} locale={zhCN}>
      <ResourceProvider
        key={`${isLoggedIn ? String(userInfo || 'authenticated') : 'anonymous'}:${offlineMode ? 'offline' : 'online'}`}
        identity={isLoggedIn ? String(userInfo || 'authenticated') : ''}
        offlineMode={offlineMode}
      >
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
                element={featureAvailable('scores', { offlineMode, offlineCapabilities })
                  ? <ScoresPage offlineMode={offlineMode} />
                  : <Navigate to={offlineDefaultPath} />}
              />
              <Route path="grade-tracking" element={featureAvailable('grade-tracking', { offlineMode, offlineCapabilities }) ? <GradeTrackingPage /> : <Navigate to={offlineDefaultPath} />} />
              <Route
                path="academic-report"
                element={featureAvailable('academic-report', { offlineMode, offlineCapabilities })
                  ? <AcademicReportPage offlineMode={offlineMode} />
                  : <Navigate to={offlineDefaultPath} />}
              />
              <Route path="experiment-courses" element={featureAvailable('experiment-courses', { offlineMode, offlineCapabilities }) ? <ExperimentCoursePage /> : <Navigate to={offlineDefaultPath} />} />
              <Route
                path="research-training"
                element={featureAvailable('research-training', { offlineMode, offlineCapabilities })
                  ? <ResearchTrainingPage offlineMode={offlineMode} />
                  : <Navigate to={offlineDefaultPath} />}
              />
              <Route path="evaluation" element={featureAvailable('evaluation', { offlineMode, offlineCapabilities }) ? <EvaluationPage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="exams" element={featureAvailable('exams', { offlineMode, offlineCapabilities }) ? <ExamPage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="timetable" element={featureAvailable('timetable', { offlineMode, offlineCapabilities }) ? <TimetablePage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="course-selection" element={featureAvailable('course-selection', { offlineMode, offlineCapabilities }) ? <CourseSelectionPage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="course-outlines" element={featureAvailable('course-outlines', { offlineMode, offlineCapabilities }) ? <CourseOutlinePage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="logs" element={featureAvailable('logs', { offlineMode, offlineCapabilities }) ? <LogsPage /> : <Navigate to={offlineDefaultPath} />} />
              <Route
                path="export"
                element={featureAvailable('export', { offlineMode, offlineCapabilities })
                  ? <ExportPage offlineMode={offlineMode} offlineCapabilities={offlineCapabilities} />
                  : <Navigate to={offlineDefaultPath} />}
              />
              <Route
                path="export/festival-activities"
                element={isExportToolAvailable('festival-activities', {
                  offlineMode,
                  offlineCapabilities,
                })
                  ? <FestivalActivitiesPage offlineMode={offlineMode} />
                  : <Navigate to="/export" />}
              />
              <Route
                path="export/academic-documents"
                element={isExportToolAvailable('academic-documents', {
                  offlineMode,
                  offlineCapabilities,
                })
                  ? <AcademicDocumentsPage />
                  : <Navigate to="/export" />}
              />
            </Route>
              </Routes>
            </Content>
          </Layout>
        </Router>
      </ResourceProvider>
    </ConfigProvider>
  );
}

export default App;
