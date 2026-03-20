import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,  // 30秒超时，避免快速切换页面时请求超时
  headers: {
    'Content-Type': 'application/json',
  },
});

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
export const login = async (username, password, remember = false) => {
  const response = await api.post('/api/login', {
    username,
    password,
    remember
  });
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
export const getScores = async (refresh = false) => {
  const response = await api.get('/api/scores', {
    params: { refresh }
  });
  return response.data;
};

// 按学期获取成绩
export const getScoresByTerm = async () => {
  const response = await api.get('/api/scores/by-term');
  return response.data;
};

// 刷新成绩
export const refreshScores = async () => {
  const response = await api.post('/api/scores/refresh');
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
export const getAcademicReport = async (refresh = false) => {
  const response = await api.get('/api/academic-report', {
    params: { refresh }
  });
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
export const refreshAcademicReport = async () => {
  const response = await api.post('/api/academic-report/refresh');
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
