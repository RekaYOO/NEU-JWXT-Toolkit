import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConfigProvider, Layout, Modal, Spin, message } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import {
  BrowserRouter as Router, Routes, Route, Navigate, useLocation,
} from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import MainLayout from './layouts/MainLayout';
import AccessLoginPage from './pages/AccessLoginPage';
import ScoresPage from './pages/ScoresPage';
import AcademicReportPage from './pages/AcademicReportPage';
import ExperimentCoursePage from './pages/ExperimentCoursePage';
import EvaluationPage from './pages/EvaluationPage';
import ExamPage from './pages/ExamPage';
import GradeTrackingPage from './pages/GradeTrackingPage';
import AuthRecoveryPage from './pages/AuthRecoveryPage';
import { nativeShellInfo } from './services/nativeBridge';
import NativeServerSettingsButton from './components/NativeServerSettingsButton';
import ResearchTrainingPage from './pages/ResearchTrainingPage';
import ExportPage from './pages/ExportPage';
import FestivalActivitiesPage from './pages/FestivalActivitiesPage';
import TimetablePage from './pages/TimetablePage';
import CourseOutlinePage from './pages/CourseOutlinePage';
import AcademicDocumentsPage from './pages/AcademicDocumentsPage';
import CourseSelectionPage from './pages/CourseSelectionPage';
import CourseSelectionWorkspacePage from './pages/CourseSelectionWorkspacePage';
import CourseSelectionArchivePage from './pages/CourseSelectionArchivePage';
import SystemSettingsPage from './pages/SystemSettingsPage';
import {
  checkStatus, getAccessStatus, getClientBootstrap, getHealth, getOfflineStatus,
  refreshWebVPNCaptcha, sendWebVPNSMSCode,
  verifyWebVPNSMSCode, cancelWebVPNSMSLogin,
  getWebVPNErrorMessage, isWebVPNFlowInvalid, isWebVPNCampusNetworkBlocked,
} from './services/api';
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
import { browserTimetableRecoveryIdentity } from './resources/BrowserTimetableStore';
import './App.css';
import { loadSetting } from './utils/settings';
import WebVPNAuthModal from './components/WebVPNAuthModal';
import { hasServiceAuthOwner } from './components/ServiceConnection';

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

const isTimetableRoute = pathname => (
  pathname === '/timetable'
  || pathname.startsWith('/timetable/')
  || (pathname === '/' && loadSetting('defaultTimetableOnOpen', false))
);

// Keep the shell compatible with older/mocked API facades during rolling
// upgrades.  The campus-network classifier is an additive API helper, so its
// absence must never abort application bootstrap or force a false login
// redirect.
const isCampusNetworkBlocked = value => (
  typeof isWebVPNCampusNetworkBlocked === 'function'
  && isWebVPNCampusNetworkBlocked(value)
);

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

function AppContent({
  isLoggedIn,
  userInfo,
  runtimeProfile,
  offlineMode,
  offlineCapabilities,
  accessState,
  isLoading,
  initialAuthSlow,
  timetableRecoveryIdentity,
  timetableRecoveryActive,
  timetableRecoveryNotice,
  onLogout,
  onLoginSuccess,
  onOfflineSuccess,
  onAccessSuccess,
  offlineDefaultPath,
  pendingAuthFlow,
  pendingCaptchaCode,
  setPendingCaptchaCode,
  pendingSmsCode,
  setPendingSmsCode,
  pendingSmsLoading,
  pendingCaptchaLoading,
  pendingSmsSent,
  onRefreshCaptcha,
  onSendSMS,
  onVerify,
  onCancel,
}) {
  const location = useLocation();
  const showTimetableRecovery = Boolean(
    timetableRecoveryIdentity
    && timetableRecoveryActive
    && !isManualLogoutActive()
    && isTimetableRoute(location.pathname),
  );
  // 本机课表恢复是“只读课表浏览”能力，不是登录态。尤其不能因为
  // 有课表快照就挂载成绩、头像、培养计划等依赖真实身份的页面。
  const canRenderCurrentRoute = isLoggedIn
    || (showTimetableRecovery && isTimetableRoute(location.pathname));

  if (isLoading && !showTimetableRecovery) {
    return (
      <div className="loading" role="status" aria-live="polite">
        <Spin size="large" />
        <span>{initialAuthSlow ? '正在恢复登录状态，请稍候' : '正在连接教务服务'}</span>
        <NativeServerSettingsButton />
      </div>
    );
  }

  if (accessState.required && !accessState.authenticated) {
    return (
      <ConfigProvider theme={appTheme} locale={zhCN}>
        <AccessLoginPage
          configured={accessState.configured}
          onSuccess={onAccessSuccess}
        />
      </ConfigProvider>
    );
  }

  return (
    <ResourceProvider
      identity={isLoggedIn
        ? String(userInfo || 'authenticated')
        : (showTimetableRecovery ? timetableRecoveryIdentity : '')}
      offlineMode={offlineMode}
      recoveryMode={showTimetableRecovery}
    >
      <Layout className="app-layout">
        <Content className="app-content">
          <Routes>
            <Route
              path="/login"
              element={
                isLoggedIn
                  ? <Navigate to="/" />
                  : <LoginPage
                    onLoginSuccess={onLoginSuccess}
                    onOfflineSuccess={onOfflineSuccess}
                  />
              }
            />
            <Route
              path="/"
              element={
                canRenderCurrentRoute
                  ? <MainLayout
                    userInfo={userInfo}
                    onLogout={onLogout}
                    runtimeProfile={runtimeProfile}
                    offlineMode={offlineMode}
                    offlineCapabilities={offlineCapabilities}
                    recoveryMode={showTimetableRecovery}
                  />
                  : <Navigate to="/login" />
              }
            >
              <Route index element={<Navigate to={showTimetableRecovery ? '/timetable' : (offlineMode ? offlineDefaultPath : (loadSetting('defaultTimetableOnOpen', false) ? '/timetable' : '/scores'))} />} />
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
              <Route path="timetable" element={featureAvailable('timetable', { offlineMode, offlineCapabilities }) ? <TimetablePage recoveryNotice={showTimetableRecovery ? timetableRecoveryNotice : ''} /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="course-selection" element={featureAvailable('course-selection', { offlineMode, offlineCapabilities }) ? <CourseSelectionPage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="course-selection/archive/:archiveId" element={featureAvailable('course-selection', { offlineMode, offlineCapabilities }) ? <CourseSelectionArchivePage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="course-selection/:batchCode/*" element={featureAvailable('course-selection', { offlineMode, offlineCapabilities }) ? <CourseSelectionWorkspacePage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="course-outlines" element={featureAvailable('course-outlines', { offlineMode, offlineCapabilities }) ? <CourseOutlinePage /> : <Navigate to={offlineDefaultPath} />} />
              <Route path="logs" element={<Navigate to="/system-settings?tab=logs" replace />} />
              <Route path="system-settings" element={featureAvailable('system-settings', { offlineMode, offlineCapabilities }) ? <SystemSettingsPage /> : <Navigate to={offlineDefaultPath} />} />
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
          <WebVPNAuthModal
            flow={pendingAuthFlow}
            captchaCode={pendingCaptchaCode}
            setCaptchaCode={setPendingCaptchaCode}
            smsCode={pendingSmsCode}
            setSmsCode={setPendingSmsCode}
            loading={pendingSmsLoading}
            captchaLoading={pendingCaptchaLoading}
            smsSent={pendingSmsSent}
            onRefreshCaptcha={onRefreshCaptcha}
            onSendSMS={onSendSMS}
            onVerify={onVerify}
            onCancel={onCancel}
          />
        </Content>
      </Layout>
    </ResourceProvider>
  );
}

function App() {
  const [timetableRecoveryIdentity] = useState(() => browserTimetableRecoveryIdentity());
  const [timetableRecoveryActive, setTimetableRecoveryActive] = useState(
    () => isTimetableRoute(window.location.pathname)
      && !isManualLogoutActive()
      && Boolean(browserTimetableRecoveryIdentity()),
  );
  const [timetableRecoveryNotice, setTimetableRecoveryNotice] = useState('');
  const recoveryMatch = window.location.pathname.match(
    /^\/auth-recovery\/([^/]+)\/?$/
  );
  const recoveryToken = recoveryMatch && nativeShellInfo()?.kind !== 'local'
    ? decodeURIComponent(recoveryMatch[1])
    : null;
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [initialAuthSlow, setInitialAuthSlow] = useState(false);
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
  const [pendingAuthFlow, setPendingAuthFlow] = useState(null);
  const [pendingCaptchaCode, setPendingCaptchaCode] = useState('');
  const [pendingSmsCode, setPendingSmsCode] = useState('');
  const [pendingSmsLoading, setPendingSmsLoading] = useState(false);
  const [pendingCaptchaLoading, setPendingCaptchaLoading] = useState(false);
  const [pendingSmsSent, setPendingSmsSent] = useState(false);

  const loadApplicationState = async () => {
    const access = await getAccessStatus();
    setAccessState(access);
    let bootstrap = null;
    let health = null;
    if (!access.required || access.authenticated) {
      try {
        bootstrap = await getClientBootstrap();
      } catch (_error) {
        // Rolling upgrades and old backends keep using the established APIs.
      }
    }
    if (!bootstrap?.runtime) {
      health = await getHealth();
    }
    setRuntimeProfile(bootstrap?.runtime?.profile || health?.profile || 'development');
    if (isCampusNetworkBlocked(bootstrap?.auth)) {
      window.dispatchEvent(new CustomEvent('neu-webvpn-campus-blocked', {
        detail: bootstrap.auth,
      }));
    }
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
          setTimetableRecoveryActive(false);
          return access;
        }
        sessionStorage.removeItem(OFFLINE_SESSION_KEY);
        setOfflineMode(false);
        setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
        const status = bootstrap?.auth?.is_logged_in
          ? bootstrap.auth
          : await checkStatus();
        setIsLoggedIn(status.is_logged_in);
        setUserInfo(status.current_user);
        if (status.is_logged_in) setTimetableRecoveryActive(false);
        else if (timetableRecoveryActive && isTimetableRoute(window.location.pathname)) {
          setTimetableRecoveryNotice('当前教务会话未确认，正在保留本机课表并继续后台恢复登录');
        }
      } else {
        const status = bootstrap?.auth?.is_logged_in
          ? bootstrap.auth
          : await checkStatus();
        setIsLoggedIn(status.is_logged_in);
        setUserInfo(status.current_user);
        if (status.is_logged_in) setTimetableRecoveryActive(false);
        else if (timetableRecoveryActive && isTimetableRoute(window.location.pathname)) {
          setTimetableRecoveryNotice('当前教务会话未确认，正在保留本机课表并继续后台恢复登录');
        }
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
        if (timetableRecoveryActive && isTimetableRoute(window.location.pathname)) {
          setTimetableRecoveryNotice('教务服务暂时无法连接，正在保留本机课表并继续后台恢复登录');
        }
      } finally {
        setIsLoading(false);
        setInitialAuthSlow(false);
      }
    };
    // 认证状态请求可能正在等待共享教务 Session 锁。三秒后只更新提示，
    // 不能在状态尚未返回时按初始 false 渲染登录路由，否则刷新业务页会
    // 被错误导航到 /login，并丢失原始深链接。
    const timer = setTimeout(() => {
      setInitialAuthSlow(true);
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

  useEffect(() => {
    const handleCampusBlock = event => {
      const detail = event.detail || {};
      message.warning({
        key: 'webvpn-campus-network-blocked',
        duration: 8,
        content: getWebVPNErrorMessage(
          detail,
          '检测到校园网环境，学校 WebVPN 不可用。当前页面与缓存已保留；请在登录页选择“校内直连”。',
        ),
      });
    };
    window.addEventListener('neu-webvpn-campus-blocked', handleCampusBlock);
    return () => window.removeEventListener('neu-webvpn-campus-blocked', handleCampusBlock);
  }, []);

  useEffect(() => {
    if (offlineMode) return undefined;
    const onPending = event => {
      const challenge = event.detail || {};
      if (!challenge.required) return;
      if (hasServiceAuthOwner(challenge.target_service)) return;
      setPendingAuthFlow(previous => ({ ...previous, ...challenge }));
    };
    const wake = () => window.dispatchEvent(new CustomEvent('neu-client-updates-wake'));
    window.addEventListener('neu-auth-pending', onPending);
    window.addEventListener('neu-auth-required', wake);
    return () => {
      window.removeEventListener('neu-auth-pending', onPending);
      window.removeEventListener('neu-auth-required', wake);
    };
  }, [offlineMode]);

  const refreshPendingCaptcha = async () => {
    if (!pendingAuthFlow) return;
    setPendingCaptchaLoading(true);
    try {
      const result = await refreshWebVPNCaptcha(pendingAuthFlow.flow_id);
      if (isCampusNetworkBlocked(result)) {
        setPendingAuthFlow(null);
        message.warning({
          duration: 8,
          content: '检测到校园网环境，学校 WebVPN 不可用。当前页面会保留；需要重新认证时请在登录页选择“校内直连”。',
        });
        return;
      }
      if (!result.success) throw Object.assign(new Error(getWebVPNErrorMessage(result, '刷新图形验证码失败')), { response: { data: result } });
      setPendingAuthFlow(previous => ({ ...previous, ...result }));
      setPendingCaptchaCode('');
      setPendingSmsSent(false);
    } catch (error) {
      if (isCampusNetworkBlocked(error)) {
        setPendingAuthFlow(null);
        message.warning({
          duration: 8,
          content: '检测到校园网环境，学校 WebVPN 不可用。当前页面会保留；需要重新认证时请在登录页选择“校内直连”。',
        });
        return;
      }
      message.error(getWebVPNErrorMessage(error, '刷新图形验证码失败'));
      if (isWebVPNFlowInvalid(error)) setPendingAuthFlow(null);
    }
    finally { setPendingCaptchaLoading(false); }
  };

  const sendPendingSms = async () => {
    if (!pendingAuthFlow || !pendingCaptchaCode.trim()) {
      message.warning('请先填写图形验证码');
      return;
    }
    setPendingSmsLoading(true);
    try {
      const result = await sendWebVPNSMSCode(pendingAuthFlow.flow_id, pendingCaptchaCode.trim());
      if (isCampusNetworkBlocked(result)) {
        setPendingAuthFlow(null);
        message.warning({
          duration: 8,
          content: '检测到校园网环境，学校 WebVPN 不可用。当前页面会保留；需要重新认证时请在登录页选择“校内直连”。',
        });
        return;
      }
      if (!result.success && result.captcha_invalid) {
        setPendingAuthFlow(previous => ({ ...previous, ...result }));
        setPendingCaptchaCode('');
        setPendingSmsCode('');
        setPendingSmsSent(false);
        message.warning(getWebVPNErrorMessage(result, '图形验证码不正确，请核对新图片'));
      } else if (!result.success) {
        if (isWebVPNFlowInvalid(result)) setPendingAuthFlow(null);
        throw Object.assign(new Error(getWebVPNErrorMessage(result, '短信验证码发送失败')), { response: { data: result } });
      }
      else { setPendingSmsSent(true); message.success('验证码已发送'); }
    } catch (error) {
      if (isCampusNetworkBlocked(error)) {
        setPendingAuthFlow(null);
        message.warning({ duration: 8, content: '校园网无法使用 WebVPN。当前页面会保留，请改用校内直连重新认证。' });
      } else {
        message.error(getWebVPNErrorMessage(error, '短信验证码发送失败'));
      }
    }
    finally { setPendingSmsLoading(false); }
  };

  const verifyPendingSms = async () => {
    if (!pendingAuthFlow || (!pendingAuthFlow.sms_verified && !pendingSmsCode.trim())) {
      message.warning('请输入短信验证码');
      return;
    }
    setPendingSmsLoading(true);
    try {
      const result = await verifyWebVPNSMSCode(pendingAuthFlow.flow_id, pendingSmsCode.trim());
      if (result.sms_verified && result.status === 'session_pending') {
        setPendingAuthFlow(previous => ({ ...previous, ...result }));
        setPendingSmsCode('');
        message.warning(result.message);
        return;
      }
      if (isCampusNetworkBlocked(result)) {
        setPendingAuthFlow(null);
        message.warning({
          duration: 8,
          content: '检测到校园网环境，学校 WebVPN 不可用。当前页面会保留；需要重新认证时请在登录页选择“校内直连”。',
        });
        return;
      }
      if (!result.success && result.status === 'captcha_invalid') {
        setPendingAuthFlow(previous => ({ ...previous, ...result }));
        setPendingCaptchaCode('');
        setPendingSmsCode('');
        setPendingSmsSent(false);
        message.warning(getWebVPNErrorMessage(result, '图形验证码不正确，请核对新图片'));
        return;
      }
      if (!result.success) {
        if (isWebVPNFlowInvalid(result)) setPendingAuthFlow(null);
        throw Object.assign(new Error(getWebVPNErrorMessage(result, '短信验证失败')), { response: { data: result } });
      }
      setPendingAuthFlow(null);
      setPendingCaptchaCode('');
      setPendingSmsCode('');
      setPendingSmsSent(false);
      if (['jwxk', 'cxcy'].includes(result.target_service || pendingAuthFlow.target_service)) {
        message.success('业务系统 WebVPN 登录已恢复');
      } else {
        handleLoginSuccess(result.username || userInfo || '已登录');
      }
    } catch (error) {
      if (isCampusNetworkBlocked(error)) {
        setPendingAuthFlow(null);
        message.warning({ duration: 8, content: '校园网无法使用 WebVPN。当前页面会保留，请改用校内直连重新认证。' });
      } else {
        message.error(getWebVPNErrorMessage(error, '短信验证失败'));
      }
    }
    finally { setPendingSmsLoading(false); }
  };

  const cancelPendingSms = async () => {
    if (pendingAuthFlow) await cancelWebVPNSMSLogin(pendingAuthFlow.flow_id).catch(() => {});
    setPendingAuthFlow(null);
    setPendingCaptchaCode('');
    setPendingSmsCode('');
    setPendingSmsSent(false);
  };

  const handleLoginSuccess = useCallback((username) => {
    clearManualLogout();
    sessionStorage.removeItem(OFFLINE_SESSION_KEY);
    setOfflineMode(false);
    setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
    setIsLoggedIn(true);
    setUserInfo(username);
    setTimetableRecoveryActive(false);
    setTimetableRecoveryNotice('');
    message.success('登录成功');
  }, []);

  const handleLogout = useCallback(() => {
    const wasOffline = offlineMode;
    markManualLogout();
    sessionStorage.removeItem(OFFLINE_SESSION_KEY);
    setOfflineMode(false);
    setOfflineCapabilities(EMPTY_OFFLINE_CAPABILITIES);
    setIsLoggedIn(false);
    setUserInfo(null);
    setTimetableRecoveryActive(false);
    setTimetableRecoveryNotice('');
    message.success(wasOffline ? '已退出离线模式' : '已登出');
  }, [offlineMode]);

  const handleOfflineSuccess = useCallback((status) => {
    sessionStorage.setItem(OFFLINE_SESSION_KEY, '1');
    setOfflineMode(true);
    setOfflineCapabilities(status);
    setIsLoggedIn(true);
    setUserInfo(status.username || '离线用户');
    setTimetableRecoveryActive(false);
    setTimetableRecoveryNotice('');
    message.success('已进入只读离线模式');
  }, []);

  const offlineDefaultPath = resolveOfflineDefaultPath(offlineCapabilities);

  if (isLoading && !(
    timetableRecoveryIdentity
    && timetableRecoveryActive
    && isTimetableRoute(window.location.pathname)
  )) {
    return (
      <div className="loading" role="status" aria-live="polite">
        <Spin size="large" />
        <span>{initialAuthSlow ? '正在恢复登录状态，请稍候' : '正在连接教务服务'}</span>
        <NativeServerSettingsButton />
      </div>
    );
  }

  if (recoveryToken) {
    return (
      <ConfigProvider theme={appTheme} locale={zhCN}>
        <AuthRecoveryPage token={recoveryToken} />
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
        <Router>
          <AppContent
            isLoggedIn={isLoggedIn}
            userInfo={userInfo}
            runtimeProfile={runtimeProfile}
            offlineMode={offlineMode}
            offlineCapabilities={offlineCapabilities}
            accessState={accessState}
            isLoading={isLoading}
            initialAuthSlow={initialAuthSlow}
            timetableRecoveryIdentity={timetableRecoveryIdentity}
            timetableRecoveryActive={timetableRecoveryActive}
            timetableRecoveryNotice={timetableRecoveryNotice}
            onLogout={handleLogout}
            onLoginSuccess={handleLoginSuccess}
            onOfflineSuccess={handleOfflineSuccess}
            onAccessSuccess={async () => {
              setIsLoading(true);
              try {
                await loadApplicationState();
              } finally {
                setIsLoading(false);
              }
            }}
            offlineDefaultPath={resolveOfflineDefaultPath(offlineCapabilities)}
            pendingAuthFlow={pendingAuthFlow}
            pendingCaptchaCode={pendingCaptchaCode}
            setPendingCaptchaCode={setPendingCaptchaCode}
            pendingSmsCode={pendingSmsCode}
            setPendingSmsCode={setPendingSmsCode}
            pendingSmsLoading={pendingSmsLoading}
            pendingCaptchaLoading={pendingCaptchaLoading}
            pendingSmsSent={pendingSmsSent}
            onRefreshCaptcha={refreshPendingCaptcha}
            onSendSMS={sendPendingSms}
            onVerify={verifyPendingSms}
            onCancel={cancelPendingSms}
          />
        </Router>
    </ConfigProvider>
  );
}

export default App;
