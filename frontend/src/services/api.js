import axios from 'axios';
import { isManualLogoutActive } from '../utils/authSessionPolicy';

const API_BASE_URL = process.env.REACT_APP_API_URL || '';  // 默认使用相对路径，支持同源部署
const OFFLINE_SESSION_KEY = 'neu_offline_mode';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,  // 30秒超时，避免快速切换页面时请求超时
  headers: {
    'Content-Type': 'application/json',
  },
});

const authRecoveryPromises = new Map();

const trySilentAuthRecovery = async (scope = 'primary') => {
  if (!authRecoveryPromises.has(scope)) {
    const statusUrl = scope === 'jwxk'
      ? '/api/course-selection/jwxk/status'
      : '/api/status';
    const recovery = api.get(statusUrl, {
      skipAuthRedirect: true,
    }).then(response => Boolean(
      scope === 'jwxk'
        ? response.data?.service_authenticated
        : response.data?.is_logged_in
    ))
      .catch(() => false)
      .finally(() => {
        authRecoveryPromises.delete(scope);
      });
    authRecoveryPromises.set(scope, recovery);
  }
  return authRecoveryPromises.get(scope);
};

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      if (error.response?.data?.code === 'ACCESS_REQUIRED') {
        window.dispatchEvent(new CustomEvent('neu-access-required'));
      } else if (
        sessionStorage.getItem(OFFLINE_SESSION_KEY) !== '1'
        && error.config?.url !== '/api/status'
        && !isManualLogoutActive()
        && !error.config?.skipAuthRedirect
        && !error.config?.url?.startsWith('/api/access/')
        && !error.config?.url?.startsWith('/api/offline/')
      ) {
        if (!error.config?._silentAuthRecoveryRetried) {
          const recoveryScope = error.config?.authRecoveryScope || 'primary';
          const recovered = await trySilentAuthRecovery(recoveryScope);
          if (recovered && !isManualLogoutActive()) {
            return api.request({
              ...error.config,
              _silentAuthRecoveryRetried: true,
            });
          }
        }
        if (isManualLogoutActive()) {
          return Promise.reject(error);
        }
        // A JWXK business session can expire while the primary JWXT session
        // remains valid.  Never turn that service-local failure into the
        // application-wide "教务会话已失效" flow.
        if (error.config?.authRecoveryScope !== 'jwxk') {
          window.dispatchEvent(new CustomEvent('neu-auth-required'));
        }
      }
    }
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) error.message = detail;
    return Promise.reject(error);
  }
);

export const getHealth = async () => {
  const response = await api.get('/api/health');
  return response.data;
};

export const getAccessStatus = async () => {
  const response = await api.get('/api/access/status');
  return response.data;
};

export const loginAccessGateway = async (password) => {
  const response = await api.post('/api/access/login', { password });
  return response.data;
};

export const logoutAccessGateway = async () => {
  const response = await api.post('/api/access/logout');
  return response.data;
};

export const shutdownRuntime = async () => {
  const health = await getHealth();
  if (!health.shutdown_token) {
    throw new Error('桌面程序未提供安全关闭令牌');
  }
  const response = await api.post('/api/runtime/shutdown', null, {
    headers: { 'X-NEU-Shutdown-Token': health.shutdown_token },
  });
  return response.data;
};

// 存储正在进行的请求控制器，用于取消请求
const pendingRequests = new Map();

/**
 * 创建带取消功能的请求配置
 * @param {string} requestId - 请求唯一标识
 * @returns {object} axios 配置
 */
export const createCancellableConfig = (requestId) => {
  // 取消之前的同名请求
  if (pendingRequests.has(requestId)) {
    pendingRequests.get(requestId).abort();
    pendingRequests.delete(requestId);
  }
  
  const controller = new AbortController();
  pendingRequests.set(requestId, controller);
  
  return {
    signal: controller.signal,
    // 请求完成后自动移除
    onDownloadProgress: () => {
      // 这里可以添加进度处理
    }
  };
};

/**
 * 取消指定请求
 * @param {string} requestId - 请求唯一标识
 */
export const cancelRequest = (requestId) => {
  if (pendingRequests.has(requestId)) {
    pendingRequests.get(requestId).abort();
    pendingRequests.delete(requestId);
    return true;
  }
  return false;
};

const runCancellable = async (requestId, request) => {
  const config = createCancellableConfig(requestId);
  const controller = pendingRequests.get(requestId);
  try {
    return await request(config);
  } finally {
    if (pendingRequests.get(requestId) === controller) {
      pendingRequests.delete(requestId);
    }
  }
};

/**
 * 取消所有请求
 */
export const cancelAllRequests = () => {
  pendingRequests.forEach((controller) => {
    controller.abort();
  });
  pendingRequests.clear();
};

// 状态检查
export const checkStatus = async () => {
  const response = await api.get('/api/status');
  return response.data;
};

// 登录
export const login = async (username, password, remember = false, networkMode = 'direct') => {
  const response = await api.post('/api/login', {
    username,
    password,
    remember,
    network_mode: networkMode,
  });
  return response.data;
};

export const startWebVPNQRLogin = async (username = '') => {
  const response = await api.post('/api/webvpn/qr/start', { username: username || null });
  return response.data;
};

export const getWebVPNQRStatus = async (flowId) => {
  const response = await api.post('/api/webvpn/qr/status', { flow_id: flowId });
  return response.data;
};

export const cancelWebVPNQRLogin = async (flowId) => {
  const response = await api.post('/api/webvpn/qr/cancel', { flow_id: flowId });
  return response.data;
};

export const startWebVPNPasswordLogin = async (username, password, remember = false) => {
  const response = await api.post('/api/webvpn/password/start', { username, password, remember });
  return response.data;
};

export const sendWebVPNSMSCode = async (flowId) => {
  const response = await api.post('/api/webvpn/sms/send', { flow_id: flowId });
  return response.data;
};

export const verifyWebVPNSMSCode = async (flowId, code, trustDevice = false) => {
  const response = await api.post('/api/webvpn/sms/verify', {
    flow_id: flowId,
    code,
    trust_device: trustDevice,
  });
  return response.data;
};

export const cancelWebVPNSMSLogin = async (flowId) => {
  const response = await api.post('/api/webvpn/sms/cancel', { flow_id: flowId });
  return response.data;
};

// 登出
export const logout = async () => {
  const response = await api.post('/api/logout');
  return response.data;
};

/**
 * 获取成绩 - 智能合并本地和远程
 * @param {boolean} refresh - 是否强制刷新
 */
export const getScores = async (refresh = false, options = {}) => {
  const response = await runCancellable('scores', (cancelConfig) => api.get(
    '/api/scores',
    { ...options, params: { refresh }, ...cancelConfig }
  ));
  return response.data;
};

// Course outlines are intentionally no-store. Only the two normalized metadata
// fields are read through the dedicated metadata endpoints below.
export const getCourseOutlineSearchSchema = async () => {
  const response = await api.get('/api/course-outlines/search-schema');
  return response.data;
};

export const searchCourseOutlines = async (payload, config = {}) => {
  const response = await api.post('/api/course-outlines/search', payload, config);
  return response.data;
};

export const getCourseOutlineOverview = async (courseCode, config = {}) => {
  const response = await api.post('/api/course-outlines/detail/overview', { course_code: courseCode }, config);
  return response.data;
};

export const getCourseOutlineSections = async (courseCode, group, config = {}) => {
  const response = await api.post('/api/course-outlines/detail/sections', {
    course_code: courseCode,
    group,
  }, config);
  return response.data;
};

export const getCourseOutlinePlanMetadata = async () => {
  const response = await api.get('/api/course-outlines/metadata/plan');
  return response.data;
};

export const getCourseOutlineMetadata = async (courseCodes) => {
  const response = await api.post('/api/course-outlines/metadata/read', {
    course_codes: courseCodes,
  });
  return response.data;
};

export const startCourseOutlineMetadataSync = async (courses, force = false) => {
  const response = await api.post('/api/course-outlines/metadata/sync', { courses, force });
  return response.data;
};

export const getCourseOutlineMetadataSyncStatus = async () => {
  const response = await api.get('/api/course-outlines/metadata/sync/status');
  return response.data;
};

export const cancelCourseOutlineMetadataSync = async () => {
  const response = await api.post('/api/course-outlines/metadata/sync/cancel');
  return response.data;
};

// 只读取当前登录账号的本地缓存，不检查教务系统会话
export const getCachedScores = async () => {
  const response = await api.get('/api/scores/cache', { skipAuthRedirect: true });
  return response.data;
};

// 按学期获取成绩
export const getScoresByTerm = async () => {
  const response = await api.get('/api/scores/by-term');
  return response.data;
};

// 刷新成绩
export const refreshScores = async (options = {}) => {
  const response = await api.post('/api/scores/refresh', null, options);
  return response.data;
};

export const getOfflineStatus = async () => {
  const response = await api.get('/api/offline/status');
  return response.data;
};

export const getOfflineScores = async () => {
  const response = await api.get('/api/offline/scores');
  return response.data;
};

export const getScoreDetailCache = async (courseCode, term) => {
  const response = await api.get('/api/scores/details/cache', {
    params: { course_code: courseCode, term },
    skipAuthRedirect: true,
  });
  return response.data;
};

export const queryScoreDetail = async (courseCode, term) => {
  const response = await api.post('/api/scores/details/query', {
    course_code: courseCode,
    term,
  });
  return response.data;
};

export const getOfflineScoreDetail = async (courseCode, term) => {
  const response = await api.get('/api/offline/scores/details', {
    params: { course_code: courseCode, term },
    skipAuthRedirect: true,
  });
  return response.data;
};

export const getOfflineAcademicReport = async () => {
  const response = await api.get('/api/offline/academic-report');
  return response.data;
};

export const getOfflineResearchTraining = async () => {
  const response = await api.get('/api/offline/research-training');
  return response.data;
};

export const getOfflineFestivalActivities = async () => {
  const response = await api.get('/api/offline/festival-activities', {
    skipAuthRedirect: true,
  });
  return response.data;
};

// ── 导出中心 API ─────────────────────────────────────────────────────────────

export const getFestivalActivities = async () => {
  const response = await api.get('/api/export/festival-activities', {
    timeout: 180000,
  });
  return response.data;
};

export const getFestivalActivitiesCache = async () => {
  const response = await api.get('/api/export/festival-activities/cache', {
    skipAuthRedirect: true,
  });
  return response.data;
};

export const deleteFestivalActivitiesCache = async () => {
  const response = await api.delete('/api/export/festival-activities/cache');
  return response.data;
};

const decodeBlobError = async (error) => {
  const blob = error.response?.data;
  if (!(blob instanceof Blob)) return error;
  try {
    const payload = JSON.parse(await blob.text());
    const detail = payload.detail || payload.message || '证书打包失败';
    const decoded = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    decoded.response = { ...error.response, data: payload };
    return decoded;
  } catch (parseError) {
    return error;
  }
};

const filenameFromDisposition = (value = '', fallback = '四节活动证书.zip') => {
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded); } catch (error) { return encoded; }
  }
  return value.match(/filename="?([^";]+)"?/i)?.[1] || fallback;
};

export const downloadFestivalCertificates = async ({ startDate, endDate }) => {
  try {
    const response = await api.post(
      '/api/export/festival-activities/certificates/archive',
      { start_date: startDate, end_date: endDate },
      { responseType: 'blob', timeout: 180000 },
    );
    return {
      blob: response.data,
      filename: filenameFromDisposition(response.headers['content-disposition']),
      succeeded: Number(response.headers['x-certificate-succeeded'] || 0),
      failed: Number(response.headers['x-certificate-failed'] || 0),
    };
  } catch (error) {
    throw await decodeBlobError(error);
  }
};

export const getAcademicDocuments = async () => {
  const response = await api.get('/api/export/academic-documents', {
    timeout: 60000,
  });
  return response.data;
};

export const generateAcademicDocument = async (documentId) => {
  try {
    const response = await api.post(
      '/api/export/academic-documents/generate',
      { document_id: documentId },
      { responseType: 'blob', timeout: 180000 },
    );
    const format = response.headers['x-academic-document-format']
      || (response.data?.type?.includes('pdf') ? 'pdf' : 'html');
    return {
      blob: response.data,
      format,
      filename: filenameFromDisposition(
        response.headers['content-disposition'],
        `教务证明.${format}`,
      ),
    };
  } catch (error) {
    throw await decodeBlobError(error);
  }
};

// ── 成绩追踪 API ─────────────────────────────────────────────────────────────

export const getGradeTrackingConfig = async () => {
  const response = await api.get('/api/grade-tracking/config');
  return response.data;
};

export const updateGradeTrackingConfig = async (config) => {
  const response = await api.put('/api/grade-tracking/config', config);
  return response.data;
};

export const setGradeTrackingEnabled = async (enabled) => {
  const response = await api.patch('/api/grade-tracking/enabled', { enabled });
  return response.data;
};

export const getGradeTrackingStatus = async () => {
  const response = await api.get('/api/grade-tracking/status');
  return response.data;
};

export const checkGradesNow = async () => {
  const response = await api.post('/api/grade-tracking/check');
  return response.data;
};

export const testGradeTrackingEmail = async () => {
  const response = await api.post('/api/grade-tracking/test-email');
  return response.data;
};

export const getSystemCacheSettings = async () => (await api.get('/api/system-settings/cache')).data;
export const updateSystemCacheSettings = async (payload) => (await api.put('/api/system-settings/cache', payload)).data;

export const getGradeTrackingRecoveryStatus = async (token) => {
  const response = await api.get(
    `/api/grade-tracking/recovery/${encodeURIComponent(token)}/status`
  );
  return response.data;
};

export const startGradeTrackingRecovery = async (token) => {
  const response = await api.post(
    `/api/grade-tracking/recovery/${encodeURIComponent(token)}/start`
  );
  return response.data;
};

export const pollGradeTrackingRecovery = async (token) => {
  const response = await api.get(
    `/api/grade-tracking/recovery/${encodeURIComponent(token)}/poll`
  );
  return response.data;
};

// 获取默认列配置
export const getDefaultColumns = async () => {
  const response = await api.get('/api/columns/default');
  return response.data;
};

// ── 日志管理 API ─────────────────────────────────────────────────────────────

/**
 * 获取日志统计摘要
 * @param {number} days - 统计天数
 */
export const getLogSummary = async (days = 7) => {
  const response = await api.get('/api/logs/summary', { params: { days } });
  return response.data;
};

/**
 * 获取日志文件列表
 * @param {string} category - 日志分类
 * @param {number} days - 天数
 */
export const getLogFiles = async (category = null, days = 7) => {
  const params = { days };
  if (category) params.category = category;
  const response = await api.get('/api/logs/files', { params });
  return response.data;
};

/**
 * 获取日志内容
 * @param {string} category - 日志分类
 * @param {string} date - 日期 (YYYY-MM-DD)
 * @param {string} level - 日志级别过滤
 * @param {string} search - 搜索关键词
 * @param {number} limit - 最大条数
 */
export const getLogContent = async (category, date, level = null, search = null, limit = 100) => {
  const params = { category, date, limit };
  if (level) params.level = level;
  if (search) params.search = search;
  const response = await api.get('/api/logs/content', { params });
  return response.data;
};

/**
 * 获取日志末尾 N 行
 * @param {string} category - 日志分类
 * @param {string} date - 日期
 * @param {number} lines - 行数
 */
export const tailLog = async (category, date, lines = 100) => {
  const response = await api.get('/api/logs/tail', {
    params: { category, date, lines }
  });
  return response.data;
};

/**
 * 搜索日志
 * @param {string} keyword - 关键词
 * @param {string} category - 分类过滤
 * @param {number} days - 天数
 * @param {number} limit - 最大结果数
 */
export const searchLogs = async (keyword, category = null, days = 7, limit = 100) => {
  const params = { keyword, days, limit };
  if (category) params.category = category;
  const response = await api.get('/api/logs/search', { params });
  return response.data;
};

/**
 * 清理旧日志
 * @param {number} keepDays - 保留天数
 */
export const cleanupLogs = async (keepDays = 30) => {
  const response = await api.delete('/api/logs/cleanup', {
    params: { keep_days: keepDays }
  });
  return response.data;
};

// ── 培养计划 API ─────────────────────────────────────────────────────────────

/**
 * 获取学业监测报告（培养计划）- 智能合并本地和远程
 * @param {boolean} refresh - 是否强制刷新
 */
export const getAcademicReport = async (refresh = false, options = {}) => {
  const response = await runCancellable(
    'academicReport',
    (cancelConfig) => api.get(
      '/api/academic-report',
      { ...options, params: { refresh }, ...cancelConfig }
    )
  );
  return response.data;
};

// 只读取当前登录账号的本地缓存，不检查教务系统会话
export const getCachedAcademicReport = async () => {
  const response = await api.get(
    '/api/academic-report/cache',
    { skipAuthRedirect: true },
  );
  return response.data;
};

/**
 * 获取培养计划摘要
 * @param {boolean} refresh - 是否强制刷新
 */
export const getAcademicReportSummary = async (refresh = false) => {
  const response = await api.get('/api/academic-report/summary', {
    params: { refresh }
  });
  return response.data;
};

/**
 * 刷新培养计划数据
 */
export const refreshAcademicReport = async (options = {}) => {
  const response = await api.post('/api/academic-report/refresh', null, options);
  return response.data;
};

// ── 统一缓存协调 API ──────────────────────────────────────────────

export const requestCacheRefresh = async (
  resource,
  { force = false, reason = 'page_swr', variant = 'default' } = {},
) => {
  const response = await api.post(
    `/api/cache/refresh/${encodeURIComponent(resource)}`,
    null,
    {
      params: { force, reason, variant },
      skipAuthRedirect: reason === 'page_swr',
    },
  );
  return response.data;
};

export const getCacheRefreshJob = async (jobId, options = {}) => {
  const response = await api.get(
    `/api/cache/jobs/${encodeURIComponent(jobId)}`,
    { ...options, skipAuthRedirect: true },
  );
  return response.data;
};

export const getCacheEvents = async (after = '', options = {}) => {
  const response = await api.get('/api/cache/events', {
    ...options,
    params: after ? { after } : {},
    skipAuthRedirect: true,
  });
  return response.data;
};

/**
 * 导出培养计划为 CSV
 */
export const exportAcademicReport = async () => {
  const response = await api.get('/api/academic-report/export');
  return response.data;
};

// ── 实验选课 API ─────────────────────────────────────────────────────────────

/**
 * 获取实验选课课程列表
 * @param {string} term - 学年学期代码，不传则自动获取当前学期
 */
export const getExperimentCourses = async (term = null) => {
  const params = {};
  if (term) params.term = term;
  const response = await api.get('/api/experiment-courses', { params });
  return response.data;
};

/**
 * 获取实验班列表
 * @param {string} taskId - 任务ID
 * @param {string} courseNo - 课程号
 * @param {string} projectCode - 实验项目代码
 * @param {string} term - 学年学期代码
 */
export const getExperimentRounds = async (taskId, courseNo, projectCode, term) => {
  const response = await api.get(`/api/experiment-courses/${taskId}/rounds`, {
    params: { course_no: courseNo, project_code: projectCode, term }
  });
  return response.data;
};

/**
 * 选择实验班
 * @param {Object} data - { term, task_id, project_code, round_id }
 */
export const selectExperimentRound = async (data) => {
  const response = await api.post('/api/experiment-courses/select', data);
  return response.data;
};

/**
 * 退选实验班
 * @param {Object} data - { term, task_id, project_code, round_id }
 */
export const deselectExperimentRound = async (data) => {
  const response = await api.post('/api/experiment-courses/deselect', data);
  return response.data;
};

// ── 科研训练 API ─────────────────────────────────────────────────────────────

export const getResearchTraining = async (params = {}) => {
  const response = await api.get('/api/research-training', { params });
  return response.data;
};

export const getResearchTrainingCache = async () => {
  const response = await api.get('/api/research-training/cache');
  return response.data;
};

let researchRefreshPromise = null;

export const refreshResearchTraining = () => {
  if (!researchRefreshPromise) {
    researchRefreshPromise = api.post(
      '/api/research-training/refresh',
      null,
      { skipAuthRedirect: true },
    )
      .then((response) => response.data)
      .finally(() => {
        researchRefreshPromise = null;
      });
  }
  return researchRefreshPromise;
};

export const setResearchTopicFavorite = async (data) => {
  const response = await api.post('/api/research-training/favorite', data);
  return response.data;
};

export const getResearchTopic = async (topicId) => {
  const response = await api.get(`/api/research-training/topics/${topicId}`);
  return response.data;
};

export const getConfirmedResearchTopics = async () => {
  const response = await api.get('/api/research-training/confirmed');
  return response.data;
};

export const enrollResearchTopic = async (data) => {
  const response = await api.post('/api/research-training/enroll', data);
  return response.data;
};

export const cancelResearchEnrollment = async (topicId) => {
  const response = await api.post('/api/research-training/cancel', {
    topic_id: topicId,
  });
  return response.data;
};

// ── 用户头像 API ─────────────────────────────────────────────────────────────

/**
 * 获取用户信息（包含头像URL）
 */
export const getUserInfo = async () => {
  const response = await api.get('/api/user/info');
  return response.data;
};

/**
 * 获取用户头像图片
 * @param {boolean} refresh - 是否强制刷新
 * @returns {Blob} 头像图片数据
 */
export const getUserAvatar = async (refresh = false) => {
  const response = await api.get('/api/user/avatar', {
    params: { refresh },
    responseType: 'blob'
  });
  return response.data;
};

// ── GPA模拟文件管理 API ───────────────────────────────────────────────────────

/**
 * 导出GPA模拟数据到服务器
 * @param {string} filename - 文件名
 * @param {Object} data - 模拟数据
 */
export const exportGPASimulation = async (filename, data) => {
  const response = await api.post('/api/gpa-simulation/export', {
    filename,
    data
  });
  return response.data;
};

/**
 * 获取GPA模拟文件列表
 */
export const listGPASimulationFiles = async () => {
  const response = await api.get('/api/gpa-simulation/files');
  return response.data;
};

/**
 * 获取指定GPA模拟文件内容
 * @param {string} filename - 文件名
 */
export const getGPASimulationFile = async (filename) => {
  const response = await api.get(`/api/gpa-simulation/file/${filename}`);
  return response.data;
};

/**
 * 删除GPA模拟文件
 * @param {string} filename - 文件名
 */
export const deleteGPASimulationFile = async (filename) => {
  const response = await api.delete(`/api/gpa-simulation/file/${filename}`);
  return response.data;
};

// ── 考试安排 API ─────────────────────────────────────────────────────────────

/**
 * 获取考试学期列表
 */
export const getExamTerms = async () => {
  const response = await api.get('/api/exams/terms');
  return response.data;
};

/**
 * 获取考试安排列表
 * @param {string} termCode - 学期代码
 */
export const getExams = async (termCode = '') => {
  const response = await api.get('/api/exams', {
    params: { term_code: termCode }
  });
  return response.data;
};

/**
 * 导出考试安排为 ICS 日历文件
 * @param {string} termCode - 学期代码
 * @returns {Blob} ICS 文件数据
 */
export const exportExamsICS = async (termCode = '') => {
  const response = await api.get('/api/exams/export-ics', {
    params: { term_code: termCode },
    responseType: 'blob',
  });
  return response.data;
};

// ── 课表查询 API ─────────────────────────────────────────────────────────────

export const getTimetableTerms = async () => {
  const response = await api.get('/api/timetable/terms');
  return response.data;
};

export const getTimetableContext = async (data) => {
  const response = await api.post('/api/timetable/context', data);
  return response.data;
};

export const searchTimetableTargets = async (data) => {
  const response = await api.post('/api/timetable/targets/search', data);
  return response.data;
};

export const getTimetableTargetFilterOptions = async (data) => {
  const response = await api.post('/api/timetable/targets/filter-options', data);
  return response.data;
};

export const getTimetableSchedule = async (data) => {
  const response = await api.post('/api/timetable/schedule', data);
  return response.data;
};

export const getPersonalTimetable = async (termCode, refresh = false) => {
  const response = await api.get('/api/timetable/personal', {
    params: { term_code: termCode, refresh },
  });
  return response.data;
};

export const checkScheduleConflicts = async (data) => {
  const response = await api.post('/api/schedule/conflicts/check', data);
  return response.data;
};

// ── 新版选课系统（jwxk）──────────────────────────────────────────────────────

export const getJwxkStatus = async (config = {}) => {
  const response = await api.get('/api/course-selection/jwxk/status', {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const updateJwxkSettings = async (networkMode) => {
  const response = await api.put('/api/course-selection/jwxk/settings', {
    network_mode: networkMode,
  });
  return response.data;
};

export const searchJwxkCourses = async (payload, config = {}) => {
  const response = await api.post('/api/course-selection/jwxk/courses/search', payload, {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const getJwxkSelected = async (batchCode, config = {}) => {
  const { includeMarket, ...requestConfig } = config;
  const response = await api.post('/api/course-selection/jwxk/selected', {
    batch_code: batchCode,
  }, {
    params: includeMarket === false ? { include_market: false } : undefined,
    authRecoveryScope: 'jwxk',
    ...requestConfig,
  });
  return response.data;
};

export const confirmJwxkBatch = async (batchCode) => {
  const response = await api.post('/api/course-selection/jwxk/batches/confirm', {
    batch_code: batchCode,
    acknowledged: true,
  }, { skipAuthRedirect: true });
  return response.data;
};

export const selectJwxkCourse = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/courses/select', payload, {
    skipAuthRedirect: true,
  });
  return response.data;
};

export const deselectJwxkCourse = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/courses/deselect', payload, {
    skipAuthRedirect: true,
  });
  return response.data;
};

export const searchJwxkCatalog = async (payload, config = {}) => {
  const response = await api.post('/api/course-selection/jwxk/catalog/search', payload, {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const getJwxkCatalogDetail = async (payload, config = {}) => {
  const response = await api.post('/api/course-selection/jwxk/catalog/detail', payload, {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const getJwxkCatalogFilterOptions = async (batchCode, config = {}) => {
  const response = await api.post('/api/course-selection/jwxk/catalog/filter-options', {
    batch_code: batchCode,
  }, {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const checkJwxkCatalogEligibility = async (batchCode, classIds, config = {}) => {
  const response = await api.post('/api/course-selection/jwxk/catalog/eligibility', {
    batch_code: batchCode,
    class_ids: classIds,
  }, {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const getJwxkCatalogArchives = async () => {
  const response = await api.get('/api/course-selection/jwxk/catalog/archives');
  return response.data;
};

export const deleteJwxkCatalogArchive = async (archiveId) => {
  const response = await api.delete(`/api/course-selection/jwxk/catalog/archives/${encodeURIComponent(archiveId)}`);
  return response.data;
};

export const getJwxkSchedule = async (batchCode, config = {}) => {
  const response = await api.post('/api/course-selection/jwxk/schedule', { batch_code: batchCode }, {
    authRecoveryScope: 'jwxk',
    ...config,
  });
  return response.data;
};

export const previewJwxkPlan = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/plan/preview', payload);
  return response.data;
};

export const readJwxkPlan = async (batchCode) => {
  const response = await api.post('/api/course-selection/jwxk/plan/read', { batch_code: batchCode });
  return response.data;
};

export const saveJwxkPlan = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/plan/save', payload);
  return response.data;
};

export const planJwxkWeights = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/weights/plan', payload);
  return response.data;
};

export const getJwxkWeightConfig = async (termCode) => {
  const response = await api.get('/api/course-selection/jwxk/weights/config', {
    params: { term_code: termCode },
  });
  return response.data;
};

export const getJwxkWeightBudget = async (batchCode) => {
  const response = await api.post('/api/course-selection/jwxk/weights/budget', { batch_code: batchCode });
  return response.data;
};

export const applyJwxkWeights = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/weights/apply', payload, {
    skipAuthRedirect: true,
  });
  return response.data;
};

export const listJwxkAutomationTasks = async (batchCode = '') => {
  const response = await api.get('/api/course-selection/jwxk/automation/tasks', {
    params: batchCode ? { batch_code: batchCode } : undefined,
  });
  return response.data;
};

export const createJwxkAutomationTask = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/automation/tasks', payload);
  return response.data;
};

export const actionJwxkAutomationTask = async (taskId, action) => {
  const response = await api.post(`/api/course-selection/jwxk/automation/tasks/${action}`, { task_id: taskId });
  return response.data;
};

export const syncJwxkAutomationTaskTimes = async (payload) => {
  const response = await api.post('/api/course-selection/jwxk/automation/tasks/sync-batch-times', payload);
  return response.data;
};

export const getJwxkAutomationSettings = async (batchCode) => {
  const response = await api.get(`/api/course-selection/jwxk/batches/${encodeURIComponent(batchCode)}/automation-settings`);
  return response.data;
};

export const updateJwxkAutomationSettings = async (batchCode, payload) => {
  const writableKeys = [
    'strategy_schedule_mode', 'rebalance_seconds', 'force_final_rebalance',
    'mail_enabled', 'notify_round_end', 'notify_final_rebalance',
    'notify_capacity_transition', 'notify_over_capacity',
    'notify_underfilled_warning', 'notify_grab_result', 'over_capacity_ratio',
  ];
  const writablePayload = Object.fromEntries(
    writableKeys.filter(key => Object.prototype.hasOwnProperty.call(payload || {}, key))
      .map(key => [key, payload[key]]),
  );
  const response = await api.put(`/api/course-selection/jwxk/batches/${encodeURIComponent(batchCode)}/automation-settings`, writablePayload);
  return response.data;
};

// ── 教学质量评价 API ─────────────────────────────────────────────────────────

/**
 * 获取评教任务列表（一级页面）
 * @param {string} xnxq - 可选学年学期；省略时由后端探测当前默认学期
 */
export const getEvaluationTasks = async (xnxq) => {
  const response = xnxq
    ? await api.get('/api/evaluation/tasks', { params: { xnxq } })
    : await api.get('/api/evaluation/tasks');
  return response.data;
};

/**
 * 获取评教任务下的课程列表（二级页面）
 * @param {string} taskId - 任务ID
 * @param {string} xnxq - 可选学年学期；省略时由后端探测当前默认学期
 */
export const getEvaluationCourses = async (taskId, xnxq) => {
  const response = xnxq
    ? await api.get(`/api/evaluation/tasks/${taskId}/courses`, { params: { xnxq } })
    : await api.get(`/api/evaluation/tasks/${taskId}/courses`);
  return response.data;
};

/**
 * 获取课程的评教指标体系
 * @param {string} xspjid - 学生评教ID
 * @param {string} taskId - 任务ID
 */
export const getEvaluationIndicators = async (xspjid, taskId) => {
  const response = await api.get(`/api/evaluation/courses/${xspjid}/indicators`, {
    params: { task_id: taskId }
  });
  return response.data;
};

/**
 * 提交评教结果（单门课程）
 * @param {string} taskId - 任务ID
 * @param {string} xspjid - 学生评教ID（课程标识）
 * @param {string} strategy - 评分策略: highest/lowest/custom
 * @param {Object} customScores - 自定义分数映射
 * @param {Object} textResults - 文本型指标内容 {zbid: text}
 * @param {boolean} dryRun - true 仅预览；必须显式 false 才真实提交
 */
export const submitEvaluation = async (
  taskId,
  xspjid,
  strategy = 'highest',
  customScores = null,
  textResults = null,
  dryRun = true,
) => {
  const data = { task_id: taskId, xspjid, strategy, dry_run: dryRun };
  if (customScores) data.custom_scores = customScores;
  if (textResults) data.text_results = textResults;
  const response = await api.post('/api/evaluation/submit', data);
  // 附加 task_id 和 xspjid 供前端使用
  const result = response.data;
  result._taskId = taskId;
  result._xspjid = xspjid;
  return result;
};

/**
 * 批量评教（指定任务下选中的未评课程）
 * @param {string} taskId - 评教任务ID
 * @param {string} strategy - 评分策略
 * @param {Object} customScores - 自定义分数映射
 * @param {string[]} xspjids - 选中的学生评教ID列表
 * @param {boolean} dryRun - true 仅预览；必须显式 false 才真实提交
 */
export const batchEvaluation = async (
  taskId,
  strategy = 'highest',
  customScores = null,
  xspjids = null,
  dryRun = true,
) => {
  const data = { task_id: taskId, strategy, dry_run: dryRun };
  if (customScores) data.custom_scores = customScores;
  if (xspjids) data.xspjids = xspjids;
  const response = await api.post('/api/evaluation/batch', data);
  return response.data;
};
