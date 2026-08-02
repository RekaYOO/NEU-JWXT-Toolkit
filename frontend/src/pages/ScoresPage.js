import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { 
  Table, Card, Statistic, Row, Col, Button, Tag, message, Alert,
  Tooltip, Dropdown, Checkbox, Space, InputNumber, Modal, Pagination, Grid,
  Segmented, Empty, Select, Form, Descriptions, Spin
} from 'antd';
import { 
  ReloadOutlined, TrophyOutlined, BookOutlined, SafetyOutlined,
  SettingOutlined, CloudSyncOutlined, DatabaseOutlined,
  CheckCircleOutlined, CalculatorOutlined, ExclamationCircleOutlined
} from '@ant-design/icons';
import GPACalculator from '../components/GPACalculator';
import { useCachedResource } from '../resources/ResourceStore';
import {
  getCacheRefreshJob,
  getOfflineScoreDetail,
  getScoreDetailCache,
  queryScoreDetail,
} from '../services/api';
import { columnSettings } from '../utils/settings';
import { compareAcademicTerms } from '../utils/termSort';
import {
  MobileDetailDrawer,
  MobileFilterButton,
  MobileFilterChips,
  MobileFilterDrawer,
} from '../components/mobile/MobileUX';
import dayjs from 'dayjs';
import './ScoresPage.css';

const DEFAULT_COLUMNS = [
  { key: 'name', title: '课程名称', visible: true, width: 200 },
  { key: 'code', title: '课程代码', visible: true, width: 120 },
  { key: 'score', title: '成绩', visible: true, width: 80 },
  { key: 'gpa', title: '绩点', visible: true, width: 80 },
  { key: 'credit', title: '学分', visible: true, width: 80 },
  { key: 'term_display', title: '学期', visible: true, width: 180 },
  { key: 'course_type', title: '课程性质', visible: true, width: 100 },
  { key: 'course_category', title: '课程类别', visible: false, width: 150 },
  { key: 'general_category', title: '通识类别', visible: false, width: 150 },
  { key: 'exam_type', title: '考核方式', visible: false, width: 100 },
  { key: 'exam_status', title: '考试状态', visible: false, width: 100 },
  { key: 'is_passed', title: '状态', visible: true, width: 80 },
  { key: 'mean_adjust_delta', title: '均分贡献', visible: false, width: 100 },
  { key: 'exclude_delta', title: '保留贡献', visible: false, width: 100 },
];

const getDefaultColumns = () => JSON.parse(JSON.stringify(DEFAULT_COLUMNS));

const NUMERIC_COLUMN_KEYS = ['score', 'gpa', 'credit'];
const IMPACT_COLUMN_KEYS = ['mean_adjust_delta', 'exclude_delta'];
const IMPACT_EPSILON = 0.00005;
const { useBreakpoint } = Grid;
const SCORE_DETAIL_JOB_POLL_MS = 600;
const SCORE_DETAIL_TERMINAL_STATES = new Set([
  'completed', 'failed', 'cancelled', 'fresh', 'throttled',
]);

const wait = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds));

const normalizeScoreDetail = (payload) => {
  if (!payload) return null;
  const detail = payload.detail
    || payload.data?.detail
    || payload.cached_detail
    || payload.data
    || payload;
  const rawItems = detail.item_scores || detail.itemScores || [];
  const itemScores = Array.isArray(rawItems)
    ? rawItems.filter(item => item && (
      item.value !== null && item.value !== undefined && item.value !== ''
      || item.name
      || item.code
    )).map(item => ({
      code: item.code ?? '',
      name: item.name ?? '',
      value: item.value,
      pass: item.pass,
      highestScoreInProportion: item.highest_score_in_proportion
        ?? item.highestScoreInProportion
        ?? false,
    }))
    : [];

  return {
    score: detail.score,
    gradePoint: detail.grade_point ?? detail.gradePoint,
    pass: detail.pass,
    itemScores,
    savedAt: detail.saved_at
      || detail.cached_at
      || detail.cache?.saved_at
      || payload.saved_at
      || payload.cached_at
      || payload.cache?.saved_at
      || null,
  };
};

const jobStoredScoreDetail = (job) => {
  const result = job?.result || job?.data || {};
  if (typeof result.stored === 'boolean') return result.stored;
  if (typeof result.cache_updated === 'boolean') return result.cache_updated;
  if (typeof result.has_details === 'boolean') return result.has_details;
  if (job?.diff?.skipped === true) return false;
  if (Number.isFinite(Number(job?.diff?.item_count))) {
    return Number(job.diff.item_count) > 0;
  }
  return null;
};

const scoreDetailErrorText = (error, fallback) => (
  error?.response?.data?.detail || error?.message || fallback
);

const IMPACT_COLUMN_HELP = {
  mean_adjust_delta: '这门课相对当前平均 GPA 的贡献量，正数表示拉高 GPA',
  exclude_delta: '保留这门课相对剔除它的贡献量，正数表示拉高 GPA',
};

const EMPTY_MOBILE_FILTERS = {
  terms: [],
  courseTypes: [],
  courseCategories: [],
  examTypes: [],
  passed: 'all',
  scoreMin: null,
  scoreMax: null,
  gpaMin: null,
  gpaMax: null,
  creditMin: null,
  creditMax: null,
  meanImpactMin: null,
  meanImpactMax: null,
  excludeImpactMin: null,
  excludeImpactMax: null,
  sort: 'none',
};

const normalizeColumnConfig = (config) => {
  const defaults = getDefaultColumns();
  if (!Array.isArray(config)) return defaults;

  const defaultMap = new Map(defaults.map(col => [col.key, col]));
  const normalized = config
    .filter(col => defaultMap.has(col.key))
    .map(col => {
      const defaultCol = defaultMap.get(col.key);
      return { ...defaultCol, ...col, title: defaultCol.title };
    });
  const existingKeys = new Set(normalized.map(col => col.key));
  const missingColumns = defaults.filter(col => !existingKeys.has(col.key));

  return [...normalized, ...missingColumns];
};

const getNumericValue = (value) => {
  const number = parseFloat(value);
  return Number.isNaN(number) ? 0 : number;
};

const getScoreNumericValue = (record, key) => {
  if (key === 'score') {
    return getNumericValue(record.score_value || record.score);
  }
  return getNumericValue(record[key]);
};

const parseRangeFilter = (value) => {
  if (!value) return {};
  try {
    const range = JSON.parse(value);
    return range && typeof range === 'object' ? range : {};
  } catch {
    return {};
  }
};

const setRangeFilterValue = (setSelectedKeys, range) => {
  const hasMin = range.min !== undefined && range.min !== null && range.min !== '';
  const hasMax = range.max !== undefined && range.max !== null && range.max !== '';
  setSelectedKeys(hasMin || hasMax ? [JSON.stringify({
    min: hasMin ? range.min : null,
    max: hasMax ? range.max : null,
  })] : []);
};

const matchesNumericRange = (filterValue, recordValue) => {
  const { min, max } = parseRangeFilter(filterValue);
  const value = Number(recordValue);
  if (!Number.isFinite(value)) return false;
  if (min !== undefined && min !== null && value < Number(min)) return false;
  if (max !== undefined && max !== null && value > Number(max)) return false;
  return true;
};

const calculateImpactScores = (scores) => {
  const totalCredits = scores.reduce((sum, score) => sum + getNumericValue(score.credit), 0);
  const totalPoints = scores.reduce(
    (sum, score) => sum + getNumericValue(score.gpa) * getNumericValue(score.credit),
    0
  );
  const currentGpa = totalCredits > 0 ? totalPoints / totalCredits : 0;

  return scores.map(score => {
    const credit = getNumericValue(score.credit);
    const gpa = getNumericValue(score.gpa);
    const meanAdjustDelta = totalCredits > 0
      ? (credit * (gpa - currentGpa)) / totalCredits
      : 0;
    const remainingCredits = totalCredits - credit;
    const excludeDelta = remainingCredits > 0
      ? currentGpa - ((totalPoints - gpa * credit) / remainingCredits)
      : null;

    return {
      ...score,
      mean_adjust_delta: meanAdjustDelta,
      exclude_delta: excludeDelta,
    };
  });
};

const formatSignedDelta = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const normalized = Math.abs(value) < IMPACT_EPSILON ? 0 : value;
  return `${normalized > 0 ? '+' : ''}${normalized.toFixed(4)}`;
};

const getImpactSign = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  if (value > IMPACT_EPSILON) return 'positive';
  if (value < -IMPACT_EPSILON) return 'negative';
  return 'zero';
};

const compareNullableNumbers = (a, b, order) => {
  const aNumber = Number(a);
  const bNumber = Number(b);
  const aValid = a !== null && a !== undefined && !Number.isNaN(aNumber);
  const bValid = b !== null && b !== undefined && !Number.isNaN(bNumber);

  if (!aValid && !bValid) return 0;
  if (!aValid) return 1;
  if (!bValid) return -1;

  return order === 'ascend' ? aNumber - bNumber : bNumber - aNumber;
};

const ScoresPage = ({ offlineMode = false }) => {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const [allScores, setAllScores] = useState([]);
  const [displayScores, setDisplayScores] = useState([]);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [dataInfo, setDataInfo] = useState({ source: 'local', is_fresh: false, last_update: null });
  
  // 更新提示
  const [updateModalVisible, setUpdateModalVisible] = useState(false);
  const [pendingUpdateData, setPendingUpdateData] = useState(null);
  
  // 列配置
  const [columnConfig, setColumnConfig] = useState(() => 
    normalizeColumnConfig(columnSettings.load(getDefaultColumns(), 'columnConfig'))
  );
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  
  // 分页
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (total) => `共 ${total} 门课程`,
  });
  const [mobileView, setMobileView] = useState('latest');
  const [mobilePage, setMobilePage] = useState(1);
  const [hasActiveFilters, setHasActiveFilters] = useState(false);
  const [gpaHelpHovered, setGpaHelpHovered] = useState(false);
  const [gpaHelpPinned, setGpaHelpPinned] = useState(false);
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  const [mobileFilters, setMobileFilters] = useState(EMPTY_MOBILE_FILTERS);
  const [mobileFilterDraft, setMobileFilterDraft] = useState(EMPTY_MOBILE_FILTERS);
  const [mobileDetail, setMobileDetail] = useState(null);
  const [scoreDetailState, setScoreDetailState] = useState({
    cached: null,
    loading: false,
    error: '',
    outcome: 'idle',
  });
  const scoreDetailRequestRef = useRef(0);

  // GPA计算器
  const [isSimulating, setIsSimulating] = useState(false);
  const gpaCalculatorRef = useRef(null);
  const dismissedRevisionRef = useRef('');
  const scoreResource = useCachedResource('scores');

  useEffect(() => () => {
    scoreDetailRequestRef.current += 1;
  }, []);

  const closeScoreDetail = useCallback(() => {
    scoreDetailRequestRef.current += 1;
    setMobileDetail(null);
    setScoreDetailState({ cached: null, loading: false, error: '', outcome: 'idle' });
  }, []);

  const openScoreDetail = useCallback(async (course) => {
    const requestId = scoreDetailRequestRef.current + 1;
    scoreDetailRequestRef.current = requestId;
    setMobileDetail(course);
    setScoreDetailState({
      cached: null,
      loading: true,
      error: '',
      outcome: 'loading-cache',
    });

    const isCurrent = () => scoreDetailRequestRef.current === requestId;
    let cached = null;
    const readCache = offlineMode ? getOfflineScoreDetail : getScoreDetailCache;

    try {
      const payload = await readCache(course.code, course.term);
      cached = normalizeScoreDetail(payload);
      if (isCurrent()) {
        setScoreDetailState({
          cached,
          loading: !offlineMode,
          error: '',
          outcome: offlineMode ? (cached?.itemScores.length ? 'cached' : 'missing') : 'querying',
        });
      }
    } catch (error) {
      if (error?.response?.status !== 404 && isCurrent()) {
        setScoreDetailState(current => ({
          ...current,
          error: scoreDetailErrorText(error, '读取分项成绩缓存失败'),
        }));
      }
      if (isCurrent()) {
        setScoreDetailState(current => ({
          ...current,
          loading: !offlineMode,
          outcome: offlineMode ? 'missing' : 'querying',
        }));
      }
    }

    if (offlineMode || !isCurrent()) {
      if (offlineMode && isCurrent()) {
        setScoreDetailState(current => ({ ...current, loading: false }));
      }
      return;
    }

    try {
      let job = await queryScoreDetail(course.code, course.term);
      const jobId = job?.job_id || job?.id;
      while (job?.status && !SCORE_DETAIL_TERMINAL_STATES.has(job.status)) {
        if (!jobId) throw new Error('后台任务未返回任务编号');
        await wait(SCORE_DETAIL_JOB_POLL_MS);
        job = await getCacheRefreshJob(jobId);
      }
      if (job?.status === 'throttled') {
        throw new Error('分项成绩查询过于频繁，请稍后重试');
      }
      if (job?.status === 'failed' || job?.status === 'cancelled') {
        throw new Error(job.error || job.error_kind || (
          job.status === 'cancelled' ? '分项成绩查询已取消' : '分项成绩查询失败'
        ));
      }

      let refreshed = null;
      try {
        refreshed = normalizeScoreDetail(await getScoreDetailCache(course.code, course.term));
      } catch (error) {
        if (error?.response?.status !== 404) throw error;
      }
      if (!isCurrent()) return;

      const nextCached = refreshed?.itemScores.length ? refreshed : cached;
      const stored = jobStoredScoreDetail(job);
      setScoreDetailState({
        cached: nextCached,
        loading: false,
        error: '',
        outcome: stored === false || !refreshed?.itemScores.length
          ? 'empty-refresh'
          : 'updated',
      });
    } catch (error) {
      if (!isCurrent()) return;
      setScoreDetailState(current => ({
        ...current,
        cached: current.cached || cached,
        loading: false,
        error: scoreDetailErrorText(error, '暂时无法获取分项成绩'),
        outcome: 'error',
      }));
    }
  }, [offlineMode]);

  const applyScorePayload = useCallback((data) => {
    if (!data?.scores) return;
    const scoresWithId = calculateImpactScores(data.scores.map((score, index) => ({
      ...score,
      _id: `${score.code}-${score.term}-${index}`,
    })));
    setAllScores(scoresWithId);
    setDisplayScores(scoresWithId);
    setDataInfo({
      source: data.source || 'local',
      is_fresh: data.cache ? !data.cache.is_stale : data.is_fresh,
      last_update: data.cache?.saved_at || data.last_update,
    });
    setDataLoaded(true);
  }, []);

  useEffect(() => {
    if (scoreResource.data) applyScorePayload(scoreResource.data);
  }, [applyScorePayload, scoreResource.data]);

  useEffect(() => {
    if (scoreResource.error) {
      message.error(`获取成绩失败: ${scoreResource.error.message}`);
      setDataLoaded(true);
    }
  }, [scoreResource.error]);

  useEffect(() => {
    if (!scoreResource.data && scoreResource.syncError) {
      message.error(`获取成绩失败: ${scoreResource.syncError}`);
      setDataLoaded(true);
    }
  }, [scoreResource.data, scoreResource.syncError]);

  useEffect(() => {
    if (
      !scoreResource.updateAvailable
      || updateModalVisible
      || dismissedRevisionRef.current === scoreResource.availableRevision
    ) return;
    setPendingUpdateData(scoreResource.availableData);
    setUpdateModalVisible(true);
  }, [
    scoreResource.availableData,
    scoreResource.updateAvailable,
    updateModalVisible,
  ]);

  // 确认更新
  const handleConfirmUpdate = async () => {
    setUpdateModalVisible(false);
    scoreResource.applyAvailable();
    if (pendingUpdateData) applyScorePayload(pendingUpdateData);
    dismissedRevisionRef.current = '';
    message.success('已显示最新成绩');
    setPendingUpdateData(null);
  };

  // 取消更新
  const handleCancelUpdate = () => {
    dismissedRevisionRef.current = scoreResource.availableRevision;
    setUpdateModalVisible(false);
    setPendingUpdateData(null);
  };

  // 手动刷新
  const handleRefresh = async () => {
    setRefreshing(true);
    message.loading('正在刷新数据...', 0);
    
    try {
      await scoreResource.refresh();
      const latest = await scoreResource.reloadAndApply();
      message.destroy();
      if (latest) applyScorePayload(latest);
      message.success('数据已刷新');
    } catch (error) {
      message.destroy();
      message.error('刷新失败: ' + error.message);
    } finally {
      setRefreshing(false);
    }
  };

  // 列配置
  const toggleColumn = (key) => {
    setColumnConfig(prev => {
      const newConfig = prev.map(col => 
        col.key === key ? { ...col, visible: !col.visible } : col
      );
      columnSettings.save(newConfig, 'columnConfig');
      return newConfig;
    });
  };
  
  const resetColumnConfig = () => {
    const defaultConfig = getDefaultColumns();
    setColumnConfig(defaultConfig);
    columnSettings.reset('columnConfig');
    message.success('已恢复默认列设置');
  };

  // 筛选选项
  const getFilterOptions = (key) => {
    const values = [...new Set(allScores.map(s => s[key]).filter(Boolean))];
    return values.map(v => ({ text: v, value: v }));
  };

  // 表格变化处理
  const handleTableChange = (newPagination, newFilters, newSorter) => {
    setPagination({
      ...pagination,
      current: newPagination.current,
      pageSize: newPagination.pageSize,
    });

    const hasFilter = (key) => Object.prototype.hasOwnProperty.call(newFilters, key);
    const readList = (key, fallback) => (hasFilter(key) ? (newFilters[key] || []) : fallback);
    const readRange = (key, boundary, fallback) => (
      hasFilter(key)
        ? (parseRangeFilter(newFilters[key]?.[0])?.[boundary] ?? null)
        : fallback
    );
    const sortField = newSorter?.field === 'term_display' ? 'term' : newSorter?.field;
    const sortDirection = newSorter?.order === 'ascend' ? 'asc' : 'desc';
    const nextFilters = {
      ...mobileFilters,
      terms: readList('term_display', mobileFilters.terms),
      courseTypes: readList('course_type', mobileFilters.courseTypes),
      courseCategories: readList('course_category', mobileFilters.courseCategories),
      examTypes: readList('exam_type', mobileFilters.examTypes),
      passed: hasFilter('is_passed')
        ? (newFilters.is_passed?.length
          ? (newFilters.is_passed[0] ? 'passed' : 'failed')
          : 'all')
        : mobileFilters.passed,
      scoreMin: readRange('score', 'min', mobileFilters.scoreMin),
      scoreMax: readRange('score', 'max', mobileFilters.scoreMax),
      gpaMin: readRange('gpa', 'min', mobileFilters.gpaMin),
      gpaMax: readRange('gpa', 'max', mobileFilters.gpaMax),
      creditMin: readRange('credit', 'min', mobileFilters.creditMin),
      creditMax: readRange('credit', 'max', mobileFilters.creditMax),
      meanImpactMin: readRange(
        'mean_adjust_delta',
        'min',
        mobileFilters.meanImpactMin,
      ),
      meanImpactMax: readRange(
        'mean_adjust_delta',
        'max',
        mobileFilters.meanImpactMax,
      ),
      excludeImpactMin: readRange(
        'exclude_delta',
        'min',
        mobileFilters.excludeImpactMin,
      ),
      excludeImpactMax: readRange(
        'exclude_delta',
        'max',
        mobileFilters.excludeImpactMax,
      ),
      sort: sortField && newSorter?.order ? `${sortField}_${sortDirection}` : 'none',
    };
    setMobileFilters(nextFilters);
    setMobileFilterDraft(nextFilters);
    applyMobileFilters(nextFilters);
  };

  // 表格列
  const tableColumns = useMemo(() => {
    return columnConfig
      .filter(col => col.visible)
      .map(col => {
        const column = {
          title: <div className="column-header">{col.title}</div>,
          dataIndex: col.key,
          key: col.key,
          width: col.width,
          sorter: true,
        };
        const filterValues = {
          term_display: mobileFilters.terms,
          course_type: mobileFilters.courseTypes,
          course_category: mobileFilters.courseCategories,
          exam_type: mobileFilters.examTypes,
          is_passed: mobileFilters.passed === 'all'
            ? []
            : [mobileFilters.passed === 'passed'],
          score: (() => {
            const { scoreMin: min, scoreMax: max } = mobileFilters;
            return min !== null || max !== null ? [JSON.stringify({ min, max })] : [];
          })(),
          gpa: (() => {
            const { gpaMin: min, gpaMax: max } = mobileFilters;
            return min !== null || max !== null ? [JSON.stringify({ min, max })] : [];
          })(),
          credit: (() => {
            const { creditMin: min, creditMax: max } = mobileFilters;
            return min !== null || max !== null ? [JSON.stringify({ min, max })] : [];
          })(),
          mean_adjust_delta: (() => {
            const { meanImpactMin: min, meanImpactMax: max } = mobileFilters;
            return min !== null || max !== null ? [JSON.stringify({ min, max })] : [];
          })(),
          exclude_delta: (() => {
            const { excludeImpactMin: min, excludeImpactMax: max } = mobileFilters;
            return min !== null || max !== null ? [JSON.stringify({ min, max })] : [];
          })(),
        };
        if (Object.prototype.hasOwnProperty.call(filterValues, col.key)) {
          column.filteredValue = filterValues[col.key];
        }

        const sortMatch = mobileFilters.sort.match(/^(.*)_(asc|desc)$/);
        const activeSortKey = sortMatch?.[1] === 'term' ? 'term_display' : sortMatch?.[1];
        if (activeSortKey === col.key) {
          column.sortOrder = sortMatch[2] === 'asc' ? 'ascend' : 'descend';
        }

        if (!NUMERIC_COLUMN_KEYS.includes(col.key) && !IMPACT_COLUMN_KEYS.includes(col.key)) {
          column.filters = getFilterOptions(col.key);
          column.filterSearch = true;
          column.onFilter = (value, record) => record[col.key] === value;
        }

        if (NUMERIC_COLUMN_KEYS.includes(col.key)) {
          column.filterDropdown = ({ setSelectedKeys, selectedKeys, confirm, clearFilters }) => {
            const range = parseRangeFilter(selectedKeys?.[0]);
            return (
              <div style={{ padding: 8 }}>
                <Space direction="vertical">
                  <InputNumber
                    placeholder="最小值"
                    value={range.min}
                    onChange={(value) => setRangeFilterValue(
                      setSelectedKeys,
                      { ...range, min: value }
                    )}
                    style={{ width: 120 }}
                  />
                  <InputNumber
                    placeholder="最大值"
                    value={range.max}
                    onChange={(value) => setRangeFilterValue(
                      setSelectedKeys,
                      { ...range, max: value }
                    )}
                    style={{ width: 120 }}
                  />
                  <Space>
                    <Button type="primary" size="small" onClick={() => confirm()}>确定</Button>
                    <Button
                      size="small"
                      onClick={() => {
                        clearFilters?.();
                        confirm();
                      }}
                    >
                      重置
                    </Button>
                  </Space>
                </Space>
              </div>
            );
          };
          column.onFilter = (value, record) => (
            matchesNumericRange(value, getScoreNumericValue(record, col.key))
          );
        }

        if (IMPACT_COLUMN_KEYS.includes(col.key)) {
          column.filterDropdown = ({ setSelectedKeys, selectedKeys, confirm, clearFilters }) => {
            const range = parseRangeFilter(selectedKeys?.[0]);
            return (
              <div style={{ padding: 8 }}>
                <Space direction="vertical">
                  <InputNumber
                    placeholder="最小值"
                    value={range.min}
                    onChange={(value) => setRangeFilterValue(
                      setSelectedKeys,
                      { ...range, min: value }
                    )}
                    style={{ width: 120 }}
                  />
                  <InputNumber
                    placeholder="最大值"
                    value={range.max}
                    onChange={(value) => setRangeFilterValue(
                      setSelectedKeys,
                      { ...range, max: value }
                    )}
                    style={{ width: 120 }}
                  />
                  <Space>
                    <Button type="primary" size="small" onClick={() => confirm()}>确定</Button>
                    <Button
                      size="small"
                      onClick={() => {
                        clearFilters?.();
                        confirm();
                      }}
                    >
                      重置
                    </Button>
                  </Space>
                </Space>
              </div>
            );
          };
          column.onFilter = (value, record) => matchesNumericRange(value, record[col.key]);
          column.render = (value) => {
            const sign = getImpactSign(value);
            const className = sign ? `impact-delta impact-${sign}` : 'impact-delta';
            return (
              <Tooltip title={IMPACT_COLUMN_HELP[col.key]}>
                <span className={className}>{formatSignedDelta(value)}</span>
              </Tooltip>
            );
          };
        }

        if (col.key === 'score') {
          column.render = (score, record) => {
            // 成绩颜色完全按照绩点显示：
            // 绩点 3.5-5.0: 绿色 (优)
            // 绩点 2.5-3.5: 蓝色 (良)
            // 绩点 1.0-2.5: 橙色 (中/合格)
            // 绩点 <1.0: 红色 (不合格)
            
            const gpa = parseFloat(record.gpa);
            let color = 'default';
            
            if (!isNaN(gpa)) {
              if (gpa >= 3.5) {
                color = 'success';
              } else if (gpa >= 2.5) {
                color = 'processing';
              } else if (gpa >= 1.0) {
                color = 'warning';
              } else {
                color = 'error';
              }
            }
            
            return (
              <button
                type="button"
                className="score-detail-trigger"
                onClick={() => openScoreDetail(record)}
                aria-label={`查看${record.name || '课程'}的分项成绩`}
              >
                <Tag color={color}>{score}</Tag>
              </button>
            );
          };
        }

        if (col.key === 'gpa') {
          column.render = (gpa, record) => (
            <button
              type="button"
              className="gpa score-detail-gpa-trigger"
              onClick={() => openScoreDetail(record)}
              aria-label={`通过绩点查看${record.name || '课程'}的分项成绩`}
            >
              {gpa?.toFixed(2)}
            </button>
          );
        }

        if (col.key === 'is_passed') {
          column.render = (passed) => (
            <Tag color={passed ? 'success' : 'error'}>{passed ? '通过' : '未通过'}</Tag>
          );
          column.filters = [{ text: '通过', value: true }, { text: '未通过', value: false }];
        }

        return column;
      });
  }, [columnConfig, allScores, mobileFilters, openScoreDetail]);

  // 列选择菜单
  const columnMenuItems = [
    ...columnConfig.map(col => ({
      key: col.key,
      label: (
        <Checkbox checked={col.visible} onChange={() => toggleColumn(col.key)}>
          {col.title}
        </Checkbox>
      ),
    })),
    { type: 'divider' },
    {
      key: 'reset',
      label: (
        <Button type="link" size="small" onClick={resetColumnConfig} style={{ padding: 0 }}>
          恢复默认
        </Button>
      ),
    },
  ];

  // 刷新按钮
  const refreshButtonText = useMemo(() => {
    if (offlineMode) return '只读离线数据';
    const lastUpdate = dataInfo.last_update ? dayjs(dataInfo.last_update) : null;
    if (dataInfo.source === 'remote' || dataInfo.is_fresh) return '已是最新';
    if (lastUpdate) return `本地数据 · ${lastUpdate.format('MM-DD')}`;
    return '刷新';
  }, [dataInfo, offlineMode]);

  const refreshButtonIcon = useMemo(() => {
    if (offlineMode) return <DatabaseOutlined />;
    if (dataInfo.source === 'remote' || dataInfo.is_fresh) return <CheckCircleOutlined />;
    return <ReloadOutlined />;
  }, [dataInfo, offlineMode]);

  // 统计
  const stats = useMemo(() => {
    if (!allScores.length) return { totalCourses: 0, passedCount: 0, failedCount: 0, totalCredits: 0 };
    
    const totalCredits = allScores.reduce((sum, s) => sum + (s.credit || 0), 0);
    
    return {
      totalCourses: allScores.length,
      passedCount: allScores.filter(s => s.is_passed).length,
      failedCount: allScores.filter(s => !s.is_passed).length,
      totalCredits: totalCredits,
    };
  }, [allScores]);

  const filteredGpaSummary = useMemo(() => {
    const gpaCourses = displayScores
      .map(score => {
        const gpa = Number.parseFloat(score.gpa);
        const credit = Number.parseFloat(score.credit);
        if (!Number.isFinite(gpa) || !Number.isFinite(credit) || credit <= 0) return null;
        return {
          gpa,
          credit,
        };
      })
      .filter(Boolean);

    const totalCredits = gpaCourses.reduce((sum, course) => sum + course.credit, 0);
    const weightedTotal = gpaCourses.reduce(
      (sum, course) => sum + course.gpa * course.credit,
      0
    );

    return {
      average: totalCredits > 0 ? weightedTotal / totalCredits : null,
      count: gpaCourses.length,
    };
  }, [displayScores]);

  const latestTerm = useMemo(() => {
    const terms = [...new Set(displayScores.map(score => score.term_display || score.term).filter(Boolean))];
    return terms.sort(compareAcademicTerms)[0] || null;
  }, [displayScores]);

  const mobileFocusedScores = useMemo(() => {
    if (mobileView === 'failed') return displayScores.filter(score => !score.is_passed);
    if (mobileView === 'latest' && latestTerm) {
      return displayScores.filter(score => (score.term_display || score.term) === latestTerm);
    }
    return displayScores;
  }, [displayScores, latestTerm, mobileView]);

  const mobileScores = useMemo(() => {
    const start = (mobilePage - 1) * 10;
    return mobileFocusedScores.slice(start, start + 10);
  }, [mobileFocusedScores, mobilePage]);

  const handleMobilePageChange = (current) => {
    setMobilePage(current);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const mobileFilterCount = useMemo(() => {
    const filters = mobileFilters;
    return [
      filters.terms.length > 0,
      filters.courseTypes.length > 0,
      filters.courseCategories.length > 0,
      filters.examTypes.length > 0,
      filters.passed !== 'all',
      filters.scoreMin !== null || filters.scoreMax !== null,
      filters.gpaMin !== null || filters.gpaMax !== null,
      filters.creditMin !== null || filters.creditMax !== null,
      filters.meanImpactMin !== null || filters.meanImpactMax !== null,
      filters.excludeImpactMin !== null || filters.excludeImpactMax !== null,
      filters.sort !== 'none',
    ].filter(Boolean).length;
  }, [mobileFilters]);

  const applyMobileFilters = useCallback((nextFilters) => {
    let filtered = [...allScores];
    const includes = (selected, value) => !selected.length || selected.includes(value);
    const inRange = (value, min, max) => {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return min === null && max === null;
      return (min === null || numeric >= Number(min))
        && (max === null || numeric <= Number(max));
    };
    filtered = filtered.filter(score => (
      includes(nextFilters.terms, score.term_display || score.term)
      && includes(nextFilters.courseTypes, score.course_type)
      && includes(nextFilters.courseCategories, score.course_category)
      && includes(nextFilters.examTypes, score.exam_type)
      && (nextFilters.passed === 'all'
        || score.is_passed === (nextFilters.passed === 'passed'))
      && inRange(score.score_value ?? score.score, nextFilters.scoreMin, nextFilters.scoreMax)
      && inRange(score.gpa, nextFilters.gpaMin, nextFilters.gpaMax)
      && inRange(score.credit, nextFilters.creditMin, nextFilters.creditMax)
      && inRange(
        score.mean_adjust_delta,
        nextFilters.meanImpactMin,
        nextFilters.meanImpactMax,
      )
      && inRange(
        score.exclude_delta,
        nextFilters.excludeImpactMin,
        nextFilters.excludeImpactMax,
      )
    ));

    if (nextFilters.sort !== 'none') {
      const direction = nextFilters.sort.endsWith('_asc') ? 1 : -1;
      const field = nextFilters.sort.replace(/_(asc|desc)$/, '');
      filtered.sort((left, right) => {
        if (field === 'term') {
          return compareAcademicTerms(
            left.term_display || left.term,
            right.term_display || right.term,
          ) * (nextFilters.sort === 'term_desc' ? 1 : -1);
        }
        if (field === 'score' || NUMERIC_COLUMN_KEYS.includes(field)) {
          const leftValue = getScoreNumericValue(left, field);
          const rightValue = getScoreNumericValue(right, field);
          return (leftValue - rightValue) * direction;
        }
        if (IMPACT_COLUMN_KEYS.includes(field)) {
          return compareNullableNumbers(
            left[field],
            right[field],
            direction === 1 ? 'ascend' : 'descend',
          );
        }
        if (field === 'is_passed') {
          return (Number(left.is_passed) - Number(right.is_passed)) * direction;
        }
        return String(left[field] || '').localeCompare(String(right[field] || ''), 'zh-CN') * direction;
      });
    }
    const active = [
      nextFilters.terms.length,
      nextFilters.courseTypes.length,
      nextFilters.courseCategories.length,
      nextFilters.examTypes.length,
      nextFilters.passed !== 'all',
      nextFilters.scoreMin !== null || nextFilters.scoreMax !== null,
      nextFilters.gpaMin !== null || nextFilters.gpaMax !== null,
      nextFilters.creditMin !== null || nextFilters.creditMax !== null,
      nextFilters.meanImpactMin !== null || nextFilters.meanImpactMax !== null,
      nextFilters.excludeImpactMin !== null || nextFilters.excludeImpactMax !== null,
      nextFilters.sort !== 'none',
    ].some(Boolean);
    setDisplayScores(filtered);
    setHasActiveFilters(active);
    setMobilePage(1);
  }, [allScores]);

  useEffect(() => {
    if (isMobile && allScores.length > 0) {
      applyMobileFilters(mobileFilters);
    }
  }, [allScores, applyMobileFilters, isMobile, mobileFilters]);

  const mobileFilterTags = useMemo(() => {
    const tags = [];
    const list = (key, label, values) => {
      if (values.length) tags.push({ key, label: `${label}：${values.join('、')}` });
    };
    list('terms', '学期', mobileFilters.terms);
    list('courseTypes', '性质', mobileFilters.courseTypes);
    list('courseCategories', '类别', mobileFilters.courseCategories);
    list('examTypes', '考核', mobileFilters.examTypes);
    if (mobileFilters.passed !== 'all') {
      tags.push({
        key: 'passed',
        label: mobileFilters.passed === 'passed' ? '仅通过' : '仅未通过',
      });
    }
    [
      ['scoreRange', '成绩', mobileFilters.scoreMin, mobileFilters.scoreMax],
      ['gpaRange', '绩点', mobileFilters.gpaMin, mobileFilters.gpaMax],
      ['creditRange', '学分', mobileFilters.creditMin, mobileFilters.creditMax],
    ].forEach(([key, label, min, max]) => {
      if (min !== null || max !== null) {
        tags.push({ key, label: `${label}：${min ?? '不限'} – ${max ?? '不限'}` });
      }
    });
    [
      ['meanImpactRange', '均分贡献', mobileFilters.meanImpactMin, mobileFilters.meanImpactMax],
      ['excludeImpactRange', '保留贡献', mobileFilters.excludeImpactMin, mobileFilters.excludeImpactMax],
    ].forEach(([key, label, min, max]) => {
      if (min !== null || max !== null) {
        tags.push({ key, label: `${label}：${min ?? '不限'} – ${max ?? '不限'}` });
      }
    });
    if (mobileFilters.sort !== 'none') {
      const labels = {
        term_desc: '学期从新到旧',
        term_asc: '学期从旧到新',
        score_desc: '成绩从高到低',
        score_asc: '成绩从低到高',
        gpa_desc: '绩点从高到低',
        gpa_asc: '绩点从低到高',
        credit_desc: '学分从高到低',
        credit_asc: '学分从低到高',
        mean_adjust_delta_desc: '均分贡献从高到低',
        mean_adjust_delta_asc: '均分贡献从低到高',
        exclude_delta_desc: '保留贡献从高到低',
        exclude_delta_asc: '保留贡献从低到高',
      };
      tags.push({ key: 'sort', label: `排序：${labels[mobileFilters.sort] || '自定义列排序'}` });
    }
    return tags;
  }, [mobileFilters]);

  const clearMobileFilterTag = (key) => {
    const patches = {
      scoreRange: { scoreMin: null, scoreMax: null },
      gpaRange: { gpaMin: null, gpaMax: null },
      creditRange: { creditMin: null, creditMax: null },
      meanImpactRange: { meanImpactMin: null, meanImpactMax: null },
      excludeImpactRange: { excludeImpactMin: null, excludeImpactMax: null },
    };
    const patch = patches[key] || { [key]: EMPTY_MOBILE_FILTERS[key] };
    const next = { ...mobileFilters, ...patch };
    setMobileFilters(next);
    setMobileFilterDraft(next);
    applyMobileFilters(next);
  };

  const commitMobileFilters = () => {
    setMobileFilters(mobileFilterDraft);
    applyMobileFilters(mobileFilterDraft);
    setMobileFilterOpen(false);
  };

  const resetMobileFilters = () => {
    const reset = { ...EMPTY_MOBILE_FILTERS };
    setMobileFilterDraft(reset);
    setMobileFilters(reset);
    applyMobileFilters(reset);
  };

  // GPA模拟
  const startSimulation = () => {
    setIsSimulating(true);
    if (gpaCalculatorRef.current) {
      gpaCalculatorRef.current.startSimulation();
    }
  };
  const exitSimulation = () => {
    setIsSimulating(false);
    if (gpaCalculatorRef.current) {
      gpaCalculatorRef.current.stopSimulation();
    }
  };
  const handleSimulatingChange = (simulating) => {
    setIsSimulating(simulating);
  };

  // 数据未加载完成时显示空状态（而不是loading）
  if (!dataLoaded) {
    return (
      <div className="scores-page">
        <Row gutter={16} className="stats-row">
          <Col xs={24} sm={12} md={6}><Card><Statistic title="课程总数" value="--" /></Card></Col>
          <Col xs={24} sm={12} md={6}><Card><Statistic title="平均绩点" value="--" /></Card></Col>
          <Col xs={24} sm={12} md={6}><Card><Statistic title="已通过" value="--" /></Card></Col>
          <Col xs={24} sm={12} md={6}><Card><Statistic title="总学分" value="--" /></Card></Col>
        </Row>
        <Card className="scores-table-card" title="成绩明细">
          <div style={{ textAlign: 'center', padding: '40px', color: '#999' }}>
            正在加载本地数据...
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="scores-page">
      {/* 统计卡片 */}
      {!isSimulating && <Row gutter={16} className="stats-row">
          <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic title="课程总数" value={stats.totalCourses} prefix={<BookOutlined />} />
          </Card>
        </Col>
          <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic
              title="平均绩点"
              value={filteredGpaSummary.average ?? '--'}
              precision={filteredGpaSummary.average === null ? undefined : 3}
              prefix={<TrophyOutlined />}
              formatter={(value) => (
                <Tooltip
                  open={hasActiveFilters && (gpaHelpHovered || gpaHelpPinned)}
                  title={`此平均绩点由当前筛选栏目下的 ${filteredGpaSummary.count} 门课程按“绩点 × 学分”加权计算。`}
                >
                  <span
                    className={hasActiveFilters ? 'filtered-gpa-value' : undefined}
                    tabIndex={hasActiveFilters ? 0 : undefined}
                    role={hasActiveFilters ? 'button' : undefined}
                    aria-label={hasActiveFilters
                      ? '查看当前筛选课程的平均绩点计算说明'
                      : undefined}
                    aria-pressed={hasActiveFilters ? gpaHelpPinned : undefined}
                    onMouseEnter={() => hasActiveFilters && setGpaHelpHovered(true)}
                    onMouseLeave={() => setGpaHelpHovered(false)}
                    onFocus={() => hasActiveFilters && setGpaHelpHovered(true)}
                    onBlur={() => {
                      setGpaHelpHovered(false);
                      setGpaHelpPinned(false);
                    }}
                    onClick={() => {
                      if (hasActiveFilters) {
                        setGpaHelpPinned((pinned) => !pinned);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (
                        hasActiveFilters
                        && (event.key === 'Enter' || event.key === ' ')
                      ) {
                        event.preventDefault();
                        setGpaHelpPinned((pinned) => !pinned);
                      }
                    }}
                  >
                    {filteredGpaSummary.average === null
                      ? '--'
                      : Number(value).toFixed(3)}
                  </span>
                </Tooltip>
              )}
              valueStyle={{ color: 'var(--color-brand)' }}
            />
          </Card>
        </Col>
          <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic title="已通过" value={stats.passedCount} prefix={<SafetyOutlined />} valueStyle={{ color: 'var(--color-success)' }} />
          </Card>
        </Col>
          <Col xs={12} sm={12} md={6}>
          <Card>
            <Statistic title="总学分" value={stats.totalCredits} precision={1} />
          </Card>
        </Col>
      </Row>}

      {/* GPA模拟计算器 */}
      <GPACalculator
        ref={gpaCalculatorRef}
        realScores={allScores}
        scoresRevision={scoreResource.displayedRevision}
        onSimulatingChange={handleSimulatingChange}
        offlineMode={offlineMode}
      />

      {/* 成绩表格 */}
      {!isSimulating && (
        <Card
          className="scores-table-card"
          title={
            <Space className="scores-card-title">
              <span>成绩明细</span>
              {!isMobile && (
                <Dropdown
                  menu={{ items: columnMenuItems }}
                  open={columnMenuOpen}
                  onOpenChange={setColumnMenuOpen}
                  placement="bottomLeft"
                  arrow
                >
                  <Button icon={<SettingOutlined />} size="small">列设置</Button>
                </Dropdown>
              )}
            </Space>
          }
          extra={
            <Space>
              <Tooltip title="进入GPA模拟模式：编辑成绩、预估GPA、导入培养计划课程">
                <Button type="primary" icon={<CalculatorOutlined />} onClick={startSimulation}>
                  GPA模拟
                </Button>
              </Tooltip>
              <Tooltip title={offlineMode
                ? '离线模式不会连接教务系统'
                : (dataInfo.last_update
                  ? `最后更新: ${dayjs(dataInfo.last_update).format('YYYY-MM-DD HH:mm')}`
                  : '点击刷新云端数据')}>
                <Button
                  type={dataInfo.source === 'remote' || dataInfo.is_fresh ? 'default' : 'primary'}
                  icon={refreshButtonIcon}
                  loading={refreshing}
                  onClick={handleRefresh}
                  disabled={offlineMode}
                >
                  {isMobile ? (offlineMode ? '离线数据' : '刷新') : refreshButtonText}
                </Button>
              </Tooltip>
            </Space>
          }
        >
          {isMobile ? (
            <>
              <div className="mobile-focus-toolbar">
                <div>
                  <strong>快速查看</strong>
                  <span>{mobileView === 'latest' ? latestTerm : '只保留当前需要的信息'}</span>
                </div>
                <Segmented
                  size="small"
                  value={mobileView}
                  options={[
                    { label: '最近', value: 'latest' },
                    { label: '未通过', value: 'failed' },
                    { label: '全部', value: 'all' },
                  ]}
                  onChange={value => {
                    setMobileView(value);
                    setMobilePage(1);
                  }}
                />
              </div>
              <div className="mobile-score-tools">
                <MobileFilterButton
                  activeCount={mobileFilterCount}
                  onClick={() => {
                    setMobileFilterDraft(mobileFilters);
                    setMobileFilterOpen(true);
                  }}
                >
                  筛选与排序
                </MobileFilterButton>
                {mobileFilterCount > 0 && (
                  <Button type="link" onClick={resetMobileFilters}>清除全部</Button>
                )}
              </div>
              {mobileFilterTags.length > 0 && (
                <MobileFilterChips
                  items={mobileFilterTags}
                  onClear={clearMobileFilterTag}
                />
              )}
              <div className="mobile-course-list" aria-label="成绩明细">
                {mobileScores.map(course => (
                  <article
                    className="mobile-course-card"
                    key={course._id}
                  >
                    <div className="mobile-course-card__header">
                      <div className="mobile-course-card__identity">
                        <strong>{course.name}</strong>
                        <span>{course.term_display || course.term}</span>
                      </div>
                      <button
                        type="button"
                        className="mobile-score-value"
                        onClick={() => openScoreDetail(course)}
                        aria-label={`查看${course.name || '课程'}的分项成绩`}
                      >
                        <span>{course.score}</span>
                        <small>成绩</small>
                      </button>
                    </div>
                    <div className="mobile-course-card__meta">
                      <span>
                        <small>绩点</small>
                        <button
                          type="button"
                          className="mobile-gpa-trigger"
                          onClick={() => openScoreDetail(course)}
                          aria-label={`通过绩点查看${course.name || '课程'}的分项成绩`}
                        >
                          {Number(course.gpa || 0).toFixed(2)}
                        </button>
                      </span>
                      <span><small>学分</small><b>{course.credit ?? '-'}</b></span>
                      <Tag color={course.is_passed ? 'success' : 'error'}>
                        {course.is_passed ? '已通过' : '未通过'}
                      </Tag>
                    </div>
                  </article>
                ))}
                {mobileScores.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的成绩" />}
              </div>
              <Pagination
                className="mobile-list-pagination"
                simple
                current={mobilePage}
                pageSize={10}
                total={mobileFocusedScores.length}
                onChange={handleMobilePageChange}
              />
            </>
          ) : (
            <Table
              columns={tableColumns}
              dataSource={displayScores}
              rowKey="_id"
              scroll={{ x: 'max-content' }}
              pagination={pagination}
              onChange={handleTableChange}
              bordered
              size="middle"
            />
          )}
        </Card>
      )}

      <MobileFilterDrawer
        open={mobileFilterOpen}
        onClose={() => setMobileFilterOpen(false)}
        onApply={commitMobileFilters}
        onReset={resetMobileFilters}
        title="成绩筛选与排序"
      >
        <Form layout="vertical">
          <Form.Item label="学期">
            <Select
              mode="multiple"
              allowClear
              value={mobileFilterDraft.terms}
              options={[...new Set(allScores
                .map(score => score.term_display || score.term)
                .filter(Boolean))]
                .sort(compareAcademicTerms)
                .map(term => ({ label: term, value: term }))}
              onChange={terms => setMobileFilterDraft(current => ({ ...current, terms }))}
            />
          </Form.Item>
          <Form.Item label="课程性质">
            <Select
              mode="multiple"
              allowClear
              value={mobileFilterDraft.courseTypes}
              options={getFilterOptions('course_type').map(option => ({
                label: option.text, value: option.value,
              }))}
              onChange={courseTypes => setMobileFilterDraft(current => ({ ...current, courseTypes }))}
            />
          </Form.Item>
          <Form.Item label="课程类别">
            <Select
              mode="multiple"
              allowClear
              value={mobileFilterDraft.courseCategories}
              options={getFilterOptions('course_category').map(option => ({
                label: option.text, value: option.value,
              }))}
              onChange={courseCategories => setMobileFilterDraft(current => ({ ...current, courseCategories }))}
            />
          </Form.Item>
          <Form.Item label="考核方式">
            <Select
              mode="multiple"
              allowClear
              value={mobileFilterDraft.examTypes}
              options={getFilterOptions('exam_type').map(option => ({
                label: option.text, value: option.value,
              }))}
              onChange={examTypes => setMobileFilterDraft(current => ({ ...current, examTypes }))}
            />
          </Form.Item>
          <Form.Item label="通过状态">
            <Segmented
              block
              value={mobileFilterDraft.passed}
              options={[
                { label: '全部', value: 'all' },
                { label: '通过', value: 'passed' },
                { label: '未通过', value: 'failed' },
              ]}
              onChange={passed => setMobileFilterDraft(current => ({ ...current, passed }))}
            />
          </Form.Item>
          {[
            ['成绩', 'scoreMin', 'scoreMax'],
            ['绩点', 'gpaMin', 'gpaMax'],
            ['学分', 'creditMin', 'creditMax'],
          ].map(([label, minKey, maxKey]) => (
            <Form.Item label={`${label}范围`} key={label}>
              <Space.Compact block>
                <InputNumber
                  placeholder="最小值"
                  value={mobileFilterDraft[minKey]}
                  onChange={value => setMobileFilterDraft(current => ({ ...current, [minKey]: value }))}
                  style={{ width: '50%' }}
                />
                <InputNumber
                  placeholder="最大值"
                  value={mobileFilterDraft[maxKey]}
                  onChange={value => setMobileFilterDraft(current => ({ ...current, [maxKey]: value }))}
                  style={{ width: '50%' }}
                />
              </Space.Compact>
            </Form.Item>
          ))}
          {[
            ['均分贡献', 'meanImpactMin', 'meanImpactMax'],
            ['保留贡献', 'excludeImpactMin', 'excludeImpactMax'],
          ].map(([label, minKey, maxKey]) => (
            <Form.Item label={`${label}范围`} key={label}>
              <Space.Compact block>
                <InputNumber
                  placeholder="最小值"
                  value={mobileFilterDraft[minKey]}
                  onChange={value => setMobileFilterDraft(current => ({
                    ...current,
                    [minKey]: value,
                  }))}
                  style={{ width: '50%' }}
                />
                <InputNumber
                  placeholder="最大值"
                  value={mobileFilterDraft[maxKey]}
                  onChange={value => setMobileFilterDraft(current => ({
                    ...current,
                    [maxKey]: value,
                  }))}
                  style={{ width: '50%' }}
                />
              </Space.Compact>
            </Form.Item>
          ))}
          <Form.Item label="排序">
            <Select
              value={mobileFilterDraft.sort}
              options={[
                { label: '不排序', value: 'none' },
                { label: '学期从新到旧', value: 'term_desc' },
                { label: '学期从旧到新', value: 'term_asc' },
                { label: '成绩从高到低', value: 'score_desc' },
                { label: '成绩从低到高', value: 'score_asc' },
                { label: '绩点从高到低', value: 'gpa_desc' },
                { label: '绩点从低到高', value: 'gpa_asc' },
                { label: '学分从高到低', value: 'credit_desc' },
                { label: '学分从低到高', value: 'credit_asc' },
                { label: '均分贡献从高到低', value: 'mean_adjust_delta_desc' },
                { label: '均分贡献从低到高', value: 'mean_adjust_delta_asc' },
                { label: '保留贡献从高到低', value: 'exclude_delta_desc' },
                { label: '保留贡献从低到高', value: 'exclude_delta_asc' },
              ]}
              onChange={sort => setMobileFilterDraft(current => ({ ...current, sort }))}
            />
          </Form.Item>
        </Form>
      </MobileFilterDrawer>

      <MobileDetailDrawer
        open={Boolean(mobileDetail)}
        onClose={closeScoreDetail}
        title={mobileDetail?.name || '课程详情'}
        width={isMobile ? '100%' : 520}
      >
        {mobileDetail && (
          <div className="score-detail-content">
            <section className="score-detail-section" aria-labelledby="score-detail-items-title">
              <div className="score-detail-section__heading">
                <div>
                  <h3 id="score-detail-items-title">分项成绩</h3>
                  {scoreDetailState.cached?.savedAt && (
                    <span>
                      最近获取：{dayjs(scoreDetailState.cached.savedAt).format('YYYY-MM-DD HH:mm')}
                    </span>
                  )}
                </div>
                {scoreDetailState.loading && <Spin size="small" />}
              </div>

              {scoreDetailState.loading && scoreDetailState.outcome === 'loading-cache' && (
                <div className="score-detail-placeholder">正在读取本地分项成绩…</div>
              )}
              {scoreDetailState.loading && scoreDetailState.outcome === 'querying' && (
                <Alert
                  type="info"
                  showIcon
                  message="正在获取最新分项成绩"
                  description={scoreDetailState.cached?.itemScores.length
                    ? '先显示已缓存数据，查询完成后会自动更新。'
                    : '总成绩不受影响，你可以关闭此页继续使用。'}
                />
              )}
              {scoreDetailState.error && (
                <Alert
                  type="warning"
                  showIcon
                  message="本次未能获取分项成绩"
                  description={scoreDetailState.cached?.itemScores.length
                    ? `${scoreDetailState.error}；下方仍显示上次成功保存的数据。`
                    : scoreDetailState.error}
                />
              )}
              {!scoreDetailState.loading
                && !scoreDetailState.error
                && scoreDetailState.outcome === 'empty-refresh' && (
                  <Alert
                    type="info"
                    showIcon
                    message="本次未返回新的分项成绩"
                    description={scoreDetailState.cached?.itemScores.length
                      ? '已保留并继续显示上次成功保存的数据。'
                      : '教务系统暂未提供这门课程的分项成绩。'}
                  />
                )}
              {!scoreDetailState.loading
                && !scoreDetailState.error
                && scoreDetailState.outcome === 'missing' && (
                  <Alert
                    type="info"
                    showIcon
                    message="没有已缓存的分项成绩"
                    description="离线模式不会连接教务系统，请在线登录后点击成绩查询。"
                  />
                )}

              {scoreDetailState.cached?.itemScores.length > 0 && (
                <div className="score-detail-items">
                  {scoreDetailState.cached.itemScores.map((item, index) => (
                    <div className="score-detail-item" key={`${item.code || 'item'}-${index}`}>
                      <div>
                        <strong>{item.name || `分项 ${index + 1}`}</strong>
                      </div>
                      <span>{item.value ?? '-'}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="score-detail-section" aria-labelledby="course-detail-title">
              <h3 id="course-detail-title">课程信息</h3>
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label="课程代码">{mobileDetail.code || '-'}</Descriptions.Item>
                <Descriptions.Item label="成绩">{mobileDetail.score ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="绩点">{mobileDetail.gpa ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="学分">{mobileDetail.credit ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="学期">{mobileDetail.term_display || mobileDetail.term || '-'}</Descriptions.Item>
                <Descriptions.Item label="课程性质">{mobileDetail.course_type || '-'}</Descriptions.Item>
                <Descriptions.Item label="课程类别">{mobileDetail.course_category || '-'}</Descriptions.Item>
                <Descriptions.Item label="通识类别">{mobileDetail.general_category || '-'}</Descriptions.Item>
                <Descriptions.Item label="考核方式">{mobileDetail.exam_type || '-'}</Descriptions.Item>
                <Descriptions.Item label="考试状态">{mobileDetail.exam_status || '-'}</Descriptions.Item>
                <Descriptions.Item label="均分贡献">{formatSignedDelta(mobileDetail.mean_adjust_delta)}</Descriptions.Item>
                <Descriptions.Item label="保留贡献">{formatSignedDelta(mobileDetail.exclude_delta)}</Descriptions.Item>
              </Descriptions>
            </section>
          </div>
        )}
      </MobileDetailDrawer>

      {/* 更新提示弹窗 */}
      <Modal
        title={
          <Space>
            <ExclamationCircleOutlined style={{ color: '#faad14' }} />
            <span>发现新成绩</span>
          </Space>
        }
        open={updateModalVisible}
        onOk={handleConfirmUpdate}
        onCancel={handleCancelUpdate}
        okText="立即更新"
        cancelText="稍后更新"
      >
        <Alert
          message="检测到云端有新成绩数据"
          description='云端成绩与本地不同，点击"立即更新"同步最新数据（包括成绩和培养计划）。'
          type="info"
          showIcon
        />
        {pendingUpdateData && (
          <div style={{ marginTop: 16 }}>
            <p>本地课程: {allScores.length} 门</p>
            <p>云端课程: {pendingUpdateData.scores?.length} 门</p>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default ScoresPage;
