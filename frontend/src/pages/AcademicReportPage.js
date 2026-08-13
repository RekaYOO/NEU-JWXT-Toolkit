import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  Table, Card, Statistic, Row, Col, Button, Tag, message, Alert,
  Tooltip, Dropdown, Checkbox, Space, InputNumber, Typography, Progress,
  Tree, Badge, Empty, Divider, Switch, Drawer, Pagination, Grid, Segmented, Input,
  Modal, Descriptions
} from 'antd';
import {
  ReloadOutlined, BookOutlined, CheckCircleOutlined, TrophyOutlined,
  SettingOutlined, DatabaseOutlined, CloudSyncOutlined, ScheduleOutlined,
  SafetyOutlined, ClockCircleOutlined, CheckOutlined, DownOutlined,
  RightOutlined, FolderOutlined, FileOutlined, PercentageOutlined,
  ExclamationCircleOutlined, FilterOutlined, DownCircleOutlined, UpCircleOutlined,
  CheckSquareOutlined, SearchOutlined
} from '@ant-design/icons';
import { useCachedResource } from '../resources/ResourceStore';
import { columnSettings, loadSetting, saveSetting } from '../utils/settings';
import {
  compareAcademicTermsNewestFirst,
  compareAcademicTermsOldestFirst,
} from '../utils/termSort';
import {
  academicTermFilterOptions,
  compareTextValues,
  uniqueFilterOptions,
} from '../utils/tableFilters';
import {
  calculateContentAwareColumnWidths,
  getAcademicRuleDeficitText,
  isElectiveCategory,
  isRequiredCategory,
} from '../utils/academicReport';
import dayjs from 'dayjs';
import { MobileDetailDrawer } from '../components/mobile/MobileUX';
import ResourceUpdateSummary from '../components/ResourceUpdateSummary';
import CourseOutlineDrawer from '../components/CourseOutlineDrawer';
import useCourseOutlineMetadata from '../hooks/useCourseOutlineMetadata';
import { summarizeAcademicReportUpdate } from '../utils/resourceUpdateSummary';
import {
  ACADEMIC_REPORT_DEFAULT_COLUMNS,
  cloneDefaultColumns,
} from '../utils/defaultColumnConfigs';
import './AcademicReportPage.css';

const { Title, Text } = Typography;
const { useBreakpoint } = Grid;

const getDefaultColumns = () => cloneDefaultColumns(ACADEMIC_REPORT_DEFAULT_COLUMNS);
const OUTLINE_DEFAULT_COLUMNS_MIGRATION_KEY = 'academicReportOutlineColumnsDefaultV1';

const parseRangeFilter = (value) => {
  try {
    return JSON.parse(value || '{}');
  } catch {
    return {};
  }
};

const matchesNumericRange = (filterValue, recordValue) => {
  const { min, max } = parseRangeFilter(filterValue);
  const value = Number(recordValue);
  if (!Number.isFinite(value)) return false;
  if (min !== null && min !== undefined && value < Number(min)) return false;
  if (max !== null && max !== undefined && value > Number(max)) return false;
  return true;
};

const numericRangeFilterDropdown = (minimumLabel, maximumLabel) => (
  { setSelectedKeys, selectedKeys, confirm, clearFilters }
) => {
  const range = parseRangeFilter(selectedKeys?.[0]);
  const update = (patch) => {
    const next = { ...range, ...patch };
    const hasValue = next.min !== null && next.min !== undefined
      || next.max !== null && next.max !== undefined;
    setSelectedKeys(hasValue ? [JSON.stringify(next)] : []);
  };
  return (
    <div style={{ padding: 8 }}>
      <Space direction="vertical">
        <InputNumber
          placeholder={minimumLabel}
          value={range.min}
          onChange={(value) => update({ min: value })}
          style={{ width: 140 }}
        />
        <InputNumber
          placeholder={maximumLabel}
          value={range.max}
          onChange={(value) => update({ max: value })}
          style={{ width: 140 }}
        />
        <Space>
          <Button type="primary" size="small" onClick={() => confirm()}>确定</Button>
          <Button
            size="small"
            onClick={() => clearFilters?.({ confirm: true, closeDropdown: true })}
          >
            重置
          </Button>
        </Space>
      </Space>
    </div>
  );
};

const textSearchFilterDropdown = (placeholder) => (
  { setSelectedKeys, selectedKeys, confirm, clearFilters }
) => (
  <div style={{ padding: 8 }}>
    <Input
      placeholder={placeholder}
      value={selectedKeys?.[0] || ''}
      onChange={(event) => setSelectedKeys(event.target.value ? [event.target.value] : [])}
      onPressEnter={() => confirm()}
      style={{ width: 200, marginBottom: 8, display: 'block' }}
    />
    <Space>
      <Button type="primary" size="small" onClick={() => confirm()}>搜索</Button>
      <Button
        size="small"
        onClick={() => clearFilters?.({ confirm: true, closeDropdown: true })}
      >
        重置
      </Button>
    </Space>
  </div>
);

const includesText = (value, query) => String(value ?? '')
  .toLocaleLowerCase('zh-CN')
  .includes(String(query ?? '').toLocaleLowerCase('zh-CN'));

const categoryPathText = (value) => Array.isArray(value) ? value.join(' > ') : String(value || '');

const courseStatusValue = (course) => {
  const rawStatus = String(course.status || '').trim();
  if (course.is_passed || ['通过', '已通过', '合格'].includes(rawStatus)) return 'passed';
  if (course.is_selected || ['已选', '已选课'].includes(rawStatus)) return 'selected';
  if (course.is_planned || ['未修', '未修读', '待选'].includes(rawStatus)) return 'planned';
  return String(course.status || 'other');
};

// 状态标签组件
const StatusTag = ({ status, isPassed, isSelected, isPlanned }) => {
  if (isPassed) {
    return <Tag color="success" icon={<CheckOutlined />}>已通过</Tag>;
  }
  if (isSelected) {
    return <Tag color="processing" icon={<ClockCircleOutlined />}>已选课</Tag>;
  }
  if (isPlanned) {
    return <Tag color="default">未修读</Tag>;
  }
  return <Tag>{status || '-'}</Tag>;
};

// 类别颜色
const CATEGORY_COLORS = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2'];

// 转换学期代码为中文学期
const formatTermCode = (termCode) => {
  if (!termCode) return '-';
  // 匹配格式：2026-2027-1 或 2026-2027-2
  const match = termCode.match(/(\d{4})-(\d{4})-(\d)/);
  if (match) {
    const [, startYear, endYear, term] = match;
    const termName = term === '1' ? '秋季' : '春季';
    return `${startYear}-${endYear}${termName}学期`;
  }
  return termCode;
};

// 递归收集所有课程
const collectAllCourses = (categories) => {
  const courses = [];
  const traverse = (nodes) => {
    nodes.forEach(node => {
      if (node.courses && node.courses.length > 0) {
        courses.push(...node.courses.map(c => ({
          ...c,
          category_name: node.name,
          category_path: node.path,
          _id: `${c.course_code}-${c.term_code || 'none'}-${Math.random().toString(36).substr(2, 9)}`
        })));
      }
      if (node.children && node.children.length > 0) {
        traverse(node.children);
      }
    });
  };
  traverse(categories);
  return courses;
};

// 根据路径过滤课程
const filterCoursesByPath = (categories, path) => {
  const courses = [];
  const traverse = (nodes) => {
    nodes.forEach(node => {
      if (node.path === path || path === 'all') {
        // 收集此节点及其所有子节点的课程
        const collectNodeCourses = (n) => {
          if (n.courses) {
            courses.push(...n.courses.map(c => ({
              ...c,
              category_name: n.name,
              category_path: n.path,
              _id: `${c.course_code}-${c.term_code || 'none'}-${Math.random().toString(36).substr(2, 9)}`
            })));
          }
          if (n.children) {
            n.children.forEach(collectNodeCourses);
          }
        };
        collectNodeCourses(node);
      } else if (node.children) {
        traverse(node.children);
      }
    });
  };
  traverse(categories);
  return courses;
};

// 查找节点信息
const findNodeByWid = (categories, wid) => {
  const traverse = (nodes) => {
    for (const node of nodes) {
      if (node.wid === wid) {
        return node;
      }
      if (node.children) {
        const found = traverse(node.children);
        if (found) return found;
      }
    }
    return null;
  };
  return traverse(categories);
};

// 构建树形数据 - 参考GPA导入页面的美化样式
const buildTreeData = (categories, expandedKeys = []) => {
  // 计算节点下的课程数量
  const countCourses = (node) => {
    let count = node.courses?.length || 0;
    if (node.children) {
      node.children.forEach(child => {
        count += countCourses(child);
      });
    }
    return count;
  };
  
  const traverse = (nodes, depth = 0) => {
    return nodes.map((node, index) => {
      const color = CATEGORY_COLORS[depth % CATEGORY_COLORS.length];
      const isExpanded = expandedKeys.includes(node.wid);
      
      // 确保数值有效，默认为0
      // earned_credits 已由后端统一为“已通过 + 已选课”。taken_credits 是旧响应
      // 兼容字段，不能在页面再次相加。
      const earnedCredits = Number(node.earned_credits ?? 0);
      const requiredCredits = node.required_credits ?? 0;
      const totalEarned = earnedCredits;
      const isCompleted = node.is_completed ?? (totalEarned >= requiredCredits);
      const hasDeficit = requiredCredits > totalEarned;
      
      // 计算课程数量
      const courseCount = countCourses(node);
      // 要求为 0 的实际课程类别也必须显示比例，例如“选修 0.00/0.00”。
      // 仅隐藏没有自身课程的纯分组节点，避免用户误以为学分数据漏载。
      const showCreditRatio =
        requiredCredits > 0 ||
        node.is_leaf ||
        (node.courses?.length || 0) > 0;
      
      const title = (
        <div className="tree-node-title">
          <span className="node-name" style={{ color }}>
            {node.name}
          </span>
          <span className="node-credits">
            {showCreditRatio && (
              <>
                <span className="credit-text earned">{totalEarned.toFixed(2)}</span>
                <span className="credit-separator">/</span>
                <span className="credit-text required">{requiredCredits.toFixed(2)}</span>
                {hasDeficit && (
                  <span className="credit-text deficit">
                    (差{(requiredCredits - totalEarned).toFixed(2)})
                  </span>
                )}
              </>
            )}
            <span className="course-count">{courseCount}门</span>
          </span>
        </div>
      );
      
      const treeNode = {
        title,
        key: node.wid,
        path: node.path,
        isLeaf: node.is_leaf && (!node.children || node.children.length === 0),
        icon: node.is_leaf ? <FileOutlined style={{ color }} /> : <FolderOutlined style={{ color }} />,
        selectable: true,
        data: node,
      };
      
      if (node.children && node.children.length > 0) {
        treeNode.children = traverse(node.children, depth + 1);
      }
      
      return treeNode;
    });
  };
  
  return traverse(categories);
};

// 获取显示名称（如果是"选修"或"必修"，则往上取一层）
const getCategoryDisplayName = (node) => {
  // 如果当前不是"选修"或"必修"，直接返回当前名称
  if (node.name !== '选修' && node.name !== '必修') {
    return node.name;
  }
  
  // 如果当前是"选修"或"必修"，取父节点名称
  if (node.path_array && node.path_array.length >= 2) {
    // 倒数第二个就是父节点名称
    return node.path_array[node.path_array.length - 2];
  }
  
  return node.name;
};

// 找到所有需要统计的类别（叶节点或要求学分>0的节点）
const findLeafCategories = (categories, filterFn) => {
  const toSummaryItem = (node, remainingCredits) => {
    const pendingCourses = (node.courses || []).filter(course =>
      course.is_selected && !course.is_passed
    );
    return {
      wid: node.wid,
      name: getCategoryDisplayName(node),
      originalName: node.name,
      path: node.path,
      path_array: node.path_array,
      required_credits: node.required_credits,
      earned_credits: node.earned_credits,
      remaining_credits: remainingCredits,
      missing_course_count: node.missing_course_count || 0,
      missing_group_count: node.missing_group_count || 0,
      pending_course_count: pendingCourses.length,
      pending_credits: pendingCourses.reduce(
        (sum, course) => sum + Number(course.credit || 0),
        0
      ),
      is_completed: node.is_completed
    };
  };

  const collect = (nodes, parentNode = null) => {
    const result = [];
    nodes.forEach(node => {
      const childItems = node.children
        ? collect(node.children, node)
        : [];

      const isDirectDoubleConstraintChild =
        Boolean(parentNode?.requires_child_minimums_and_total);
      if (!filterFn(node) && !isDirectDoubleConstraintChild) {
        result.push(...childItems);
        return;
      }

      if (node.required_credits <= 0) {
        result.push(...childItems);
        return;
      }

      const childrenAllZero = node.children && node.children.every(child =>
        child.required_credits === 0 && (!child.children || child.children.length === 0)
      );
      const hasCountRuleDeficit =
        (node.missing_course_count || 0) > 0 ||
        (node.missing_group_count || 0) > 0;
      const childCreditDeficit = childItems.reduce(
        (sum, item) => sum + (item.remaining_credits || 0),
        0
      );

      // 双重约束父组及其直接子类采用自底向上的增量差额：孙类先
      // 计入，当前层只补尚未覆盖的部分，从而既不遗漏非叶子子类的
      // 最低要求，也不会与父级总量要求重复累计。
      const isDoubleConstraintLevel =
        node.requires_child_minimums_and_total ||
        isDirectDoubleConstraintChild;
      if (isDoubleConstraintLevel) {
        const ownCreditDeficit = node.requires_child_minimums_and_total
          ? (node.aggregate_remaining_credits || 0)
          : (node.remaining_credits || 0);
        const incrementalDeficit = Math.max(0, ownCreditDeficit - childCreditDeficit);
        if (incrementalDeficit > 0 || hasCountRuleDeficit) {
          result.push(toSummaryItem(node, incrementalDeficit));
        }
        result.push(...childItems);
        return;
      }

      const shouldShowParentRule =
        (node.remaining_credits || 0) === 0 && hasCountRuleDeficit;
      if (!node.children || node.children.length === 0 || childrenAllZero || shouldShowParentRule) {
        // “缺学分”用于回答还需要选什么课。后端 remaining_credits 已把
        // 已选课计入；仅有教务系统最终完成状态为 false（例如课程尚未通过）
        // 时，不应继续把该类别列为选课缺口。
        if (node.remaining_credits > 0 || hasCountRuleDeficit) {
          result.push(toSummaryItem(node, node.remaining_credits || 0));
        }
      }
      result.push(...childItems);
    });
    return result;
  };

  const result = collect(categories);
  return result.sort((a, b) => b.remaining_credits - a.remaining_credits);
};

// 找到所有需要统计的选修类别
const findElectiveLeafCategories = (categories) => {
  return findLeafCategories(categories, isElectiveCategory);
};

// 找到所有需要统计的必修类别
const findRequiredLeafCategories = (categories) => {
  return findLeafCategories(categories, isRequiredCategory);
};

// 计算选修类别还差多少学分
const calcElectiveRemainingCredits = (categories) => {
  const leafCategories = findElectiveLeafCategories(categories);
  return leafCategories.reduce((sum, cat) => sum + (cat.remaining_credits || 0), 0);
};

// 计算必修类别还差多少学分
const calcRequiredRemainingCredits = (categories) => {
  const leafCategories = findRequiredLeafCategories(categories);
  return leafCategories.reduce((sum, cat) => sum + (cat.remaining_credits || 0), 0);
};

const AcademicReportPage = ({ offlineMode = false }) => {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  // 数据状态
  const [report, setReport] = useState(null);
  const [categories, setCategories] = useState([]);
  const [allCourses, setAllCourses] = useState([]);
  const [displayCourses, setDisplayCourses] = useState([]);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [dataInfo, setDataInfo] = useState({ source: 'local', is_fresh: false, last_update: null });
  
  // 树形导航状态
  const [selectedKeys, setSelectedKeys] = useState([]);
  const [expandedKeys, setExpandedKeys] = useState([]);
  const [autoExpandParent, setAutoExpandParent] = useState(true);
  const [mobileCategoryOpen, setMobileCategoryOpen] = useState(false);
  
  // 表格筛选状态
  
  // 缺学分列表展开状态
  const [showAllIncomplete, setShowAllIncomplete] = useState(false);
  
  // 悬挂式学分统计卡片展开状态
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [headerPortalTarget, setHeaderPortalTarget] = useState(null);

  useEffect(() => {
    setHeaderPortalTarget(document.getElementById('workspace-header-center'));
  }, []);
  
  // 列配置
  const [columnConfig, setColumnConfig] = useState(() => {
    const defaults = getDefaultColumns();
    const loaded = columnSettings.load(defaults, 'academicReportColumnConfig');
    const saved = Array.isArray(loaded) ? loaded : defaults;
    if (loadSetting(OUTLINE_DEFAULT_COLUMNS_MIGRATION_KEY, false)) return saved;
    const migrated = saved.map(column => (
      ['assessment_method', 'grading_scale'].includes(column.key)
        ? { ...column, visible: true }
        : column
    ));
    columnSettings.save(migrated, 'academicReportColumnConfig');
    saveSetting(OUTLINE_DEFAULT_COLUMNS_MIGRATION_KEY, true);
    return migrated;
  });
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const openTableFiltersRef = useRef(new Set());
  const pendingOutlineFilterOptionsRef = useRef(null);
  const [outlineFilterOptions, setOutlineFilterOptions] = useState({
    assessment_method: [],
    grading_scale: [],
  });
  
  // 分页
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 20,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (total) => `共 ${total} 门课程`,
  });
  const [mobilePlanView, setMobilePlanView] = useState('pending');
  const [mobilePage, setMobilePage] = useState(1);
  const [courseSearchOpen, setCourseSearchOpen] = useState(false);
  const [courseSearch, setCourseSearch] = useState('');
  const [mobileCourseDetail, setMobileCourseDetail] = useState(null);
  const [outlineCourse, setOutlineCourse] = useState(null);
  const reportResource = useCachedResource('academic-report');
  const initializedRef = useRef(false);
  const promptedRevisionRef = useRef('');
  const [pendingPlanUpdate, setPendingPlanUpdate] = useState(null);

  const applyReportPayload = useCallback((reportData, { initial = false } = {}) => {
    if (!reportData) return;
    const nextCategories = reportData.categories || [];
    const all = collectAllCourses(nextCategories);
    setReport(reportData);
    setCategories(nextCategories);
    setAllCourses(all);

    if (!initial && selectedKeys.length) {
      const selected = findNodeByWid(nextCategories, selectedKeys[0]);
      setDisplayCourses(selected?.path
        ? filterCoursesByPath(nextCategories, selected.path)
        : all);
    } else {
      setDisplayCourses(all);
    }

    setDataInfo({
      source: reportData.source || 'local',
      is_fresh: reportData.cache ? !reportData.cache.is_stale : reportData.is_fresh,
      last_update: reportData.cache?.saved_at || reportData.last_update,
    });

    if (initial) {
      const collectAllKeys = (nodes) => {
        const keys = [];
        const traverse = (list) => list.forEach(node => {
          keys.push(node.wid);
          if (node.children?.length) traverse(node.children);
        });
        traverse(nodes || []);
        return keys;
      };
      setExpandedKeys(collectAllKeys(nextCategories));
    }
    setDataLoaded(true);
  }, [selectedKeys]);

  useEffect(() => {
    if (!reportResource.data) return;
    const initial = !initializedRef.current;
    initializedRef.current = true;
    applyReportPayload(reportResource.data, { initial });
  }, [applyReportPayload, reportResource.data]);

  const outlineColumnsEnabled = columnConfig.some(column => (
    ['assessment_method', 'grading_scale'].includes(column.key) && column.visible
  ));
  const {
    metadata: outlineMetadata,
    syncing: outlineSyncing,
    status: outlineSyncStatus,
    retryFailed: retryOutlineMetadata,
  } = useCourseOutlineMetadata({
    courses: allCourses,
    enabled: outlineColumnsEnabled,
    offlineMode,
  });

  useEffect(() => {
    const nextOptions = {
      assessment_method: uniqueFilterOptions(allCourses.map(course => (
        outlineMetadata[course.course_code]?.assessment_method || course.exam_type
      ))),
      grading_scale: uniqueFilterOptions(allCourses.map(course => (
        outlineMetadata[course.course_code]?.grading_scale
      ))),
    };
    if (openTableFiltersRef.current.size > 0) {
      pendingOutlineFilterOptionsRef.current = nextOptions;
      return;
    }
    pendingOutlineFilterOptionsRef.current = null;
    setOutlineFilterOptions(nextOptions);
  }, [allCourses, outlineMetadata]);

  useEffect(() => {
    if (reportResource.error) {
      console.error('加载培养计划失败:', reportResource.error);
      message.error('加载培养计划失败');
      setDataLoaded(true);
    }
  }, [reportResource.error]);

  useEffect(() => {
    if (!reportResource.data && reportResource.syncError) {
      message.error(`加载培养计划失败: ${reportResource.syncError}`);
      setDataLoaded(true);
    }
  }, [reportResource.data, reportResource.syncError]);

  useEffect(() => {
    if (
      !reportResource.updateAvailable
      || !reportResource.availableRevision
      || promptedRevisionRef.current === reportResource.availableRevision
    ) return undefined;
    promptedRevisionRef.current = reportResource.availableRevision;
    const updateSummary = summarizeAcademicReportUpdate(
      reportResource.data,
      reportResource.availableData,
    );
    if (!updateSummary.length) {
      // The cache revision changed only because the remote endpoint reordered
      // equivalent nodes (or changed numeric serialization). Apply silently.
      reportResource.applyAvailable();
      applyReportPayload(reportResource.availableData);
      return undefined;
    }
    setPendingPlanUpdate({
      revision: reportResource.availableRevision,
      data: reportResource.availableData,
      summary: updateSummary,
    });
    return undefined;
  }, [
    applyReportPayload,
    reportResource.data,
    reportResource.availableData,
    reportResource.availableRevision,
    reportResource.updateAvailable,
    reportResource.applyAvailable,
  ]);

  // 刷新数据
  const handleRefresh = async () => {
    if (offlineMode) return;
    setRefreshing(true);
    message.loading('正在刷新...', 0);
    
    try {
      await reportResource.refresh();
      const latest = await reportResource.reloadAndApply();
      if (latest) applyReportPayload(latest);
      message.destroy();
      message.success('数据已刷新');
    } catch (error) {
      message.destroy();
      message.error('刷新失败: ' + error.message);
    } finally {
      setRefreshing(false);
    }
  };

  const applyPendingPlanUpdate = () => {
    const update = pendingPlanUpdate;
    setPendingPlanUpdate(null);
    if (!update?.data) return;
    reportResource.applyData(update.data);
    applyReportPayload(update.data);
  };

  // 处理树节点选择
  const onSelect = (selectedKeys, info) => {
    setSelectedKeys(selectedKeys);
    if (info.selected && info.node) {
      const path = info.node.props?.path || info.node.path;
      const nodeData = info.node.props?.data || info.node.data;
      
      if (path) {
        const courses = filterCoursesByPath(categories, path);
        setDisplayCourses(courses);
      }
    } else {
      setDisplayCourses(allCourses);
    }
    setPagination(prev => ({ ...prev, current: 1 }));
    setMobilePage(1);
    if (isMobile) setMobileCategoryOpen(false);
  };

  // 处理树节点展开
  const onExpand = (expandedKeys) => {
    setExpandedKeys(expandedKeys);
    setAutoExpandParent(false);
  };

  // 切换列显示
  const toggleColumn = (key) => {
    setColumnConfig(prev => {
      const newConfig = prev.map(col => 
        col.key === key ? { ...col, visible: !col.visible } : col
      );
      columnSettings.save(newConfig, 'academicReportColumnConfig');
      return newConfig;
    });
  };
  
  const resetColumnConfig = () => {
    const defaultConfig = getDefaultColumns();
    setColumnConfig(defaultConfig);
    columnSettings.reset('academicReportColumnConfig');
    message.success('已恢复默认列设置');
  };

  // 筛选课程
  const filteredCourses = useMemo(() => {
    const query = courseSearch.trim().toLocaleLowerCase('zh-CN');
    if (!query) return [...displayCourses];

    return displayCourses.filter(course => [
      course.course_name,
      course.course_code,
      course.course_nature,
      course.term_code,
      formatTermCode(course.term_code),
    ].some(value => String(value || '').toLocaleLowerCase('zh-CN').includes(query)));
  }, [displayCourses, courseSearch]);

  const orderedFilteredCourses = useMemo(() => (
    [...filteredCourses].sort((left, right) => compareAcademicTermsNewestFirst(
      left.term_code,
      right.term_code,
    ))
  ), [filteredCourses]);

  // 处理表格变化
  const handleTableChange = (newPagination, newFilters, newSorter) => {
    setPagination({
      ...pagination,
      current: newPagination.current,
      pageSize: newPagination.pageSize,
    });
  };

  // 获取当前选中的类别显示名称（显示完整路径）
  const selectedCategoryName = useMemo(() => {
    if (selectedKeys.length === 0) return null;
    const node = findNodeByWid(categories, selectedKeys[0]);
    if (!node) return null;
    // 显示完整路径
    if (node.path_array && node.path_array.length > 0) {
      return node.path_array.join(' > ');
    }
    return node.name;
  }, [selectedKeys, categories]);

  // 构建表格列
  const tableColumns = useMemo(() => {
    // 类别路径属于补充定位信息；开启后始终置于最后，兼容旧账号保存的列顺序。
    const visibleConfig = columnConfig
      .filter(col => col.visible)
      .sort((left, right) => Number(left.key === 'category_path') - Number(right.key === 'category_path'));
    const displayValue = (key, course) => {
      if (key === 'status') {
        const status = courseStatusValue(course);
        return { passed: '已通过', selected: '已选课', planned: '未修读' }[status] || course.status || '-';
      }
      if (key === 'category_path') return categoryPathText(course.category_path) || '-';
      if (key === 'term_code') return formatTermCode(course.term_code);
      if (key === 'is_passed') return course.is_passed ? '是' : '否';
      if (key === 'is_core') return course.is_core ? '核心' : '-';
      if (key === 'assessment_method' || key === 'grading_scale') {
        const metadata = outlineMetadata[course.course_code];
        return metadata?.[key] || (key === 'assessment_method' ? course.exam_type : '') || '-';
      }
      if (key === 'course_name') {
        // 名称和代码分两行显示，列宽取较长的一行，不把两行长度相加。
        return [course.course_name || '-', course.course_code || '无课程代码'];
      }
      return course[key] ?? '-';
    };
    const canMeasureWithCanvas = typeof document !== 'undefined'
      && !String(globalThis.navigator?.userAgent || '').toLowerCase().includes('jsdom');
    const measurementContext = !canMeasureWithCanvas
      ? null
      : document.createElement('canvas').getContext?.('2d');
    if (measurementContext) {
      measurementContext.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    }
    const measureText = (value) => {
      const context = measurementContext;
      if (!context) return String(value ?? '').length * 14;
      return context.measureText(String(value ?? '')).width;
    };
    const measuredConfig = calculateContentAwareColumnWidths(
      visibleConfig.map(col => ({
        ...col,
        hasControls: ['course_name', 'course_code', 'credit', 'status', 'score', 'course_nature',
          'is_passed', 'category_path', 'term_code', 'is_core'].includes(col.key),
      })),
      allCourses,
      displayValue,
      measureText,
    );

    return measuredConfig
      .map(col => {
        const column = {
          title: col.title,
          dataIndex: col.key,
          key: col.key,
          width: col.width,
          sorter: (a, b) => {
            if (col.key === 'credit') {
              return Number(a.credit || 0) - Number(b.credit || 0);
            }
            if (col.key === 'term_code') {
              return compareAcademicTermsOldestFirst(a.term_code, b.term_code);
            }
            if (col.key === 'is_passed' || col.key === 'is_core') {
              return Number(Boolean(a[col.key])) - Number(Boolean(b[col.key]));
            }
            if (col.key === 'status') {
              return compareTextValues(courseStatusValue(a), courseStatusValue(b));
            }
            if (col.key === 'category_path') {
              return compareTextValues(categoryPathText(a.category_path), categoryPathText(b.category_path));
            }
            if (col.key === 'assessment_method' || col.key === 'grading_scale') {
              const leftMetadata = outlineMetadata[a.course_code];
              const rightMetadata = outlineMetadata[b.course_code];
              const leftValue = leftMetadata?.[col.key]
                || (col.key === 'assessment_method' ? a.exam_type : '');
              const rightValue = rightMetadata?.[col.key]
                || (col.key === 'assessment_method' ? b.exam_type : '');
              return compareTextValues(leftValue, rightValue);
            }
            if (col.key === 'score') {
              const left = Number(a.score);
              const right = Number(b.score);
              if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
            }
            return compareTextValues(a[col.key], b[col.key]);
          },
          onFilterDropdownOpenChange: open => {
            if (open) {
              openTableFiltersRef.current.add(col.key);
              return;
            }
            openTableFiltersRef.current.delete(col.key);
            if (openTableFiltersRef.current.size === 0 && pendingOutlineFilterOptionsRef.current) {
              const pending = pendingOutlineFilterOptionsRef.current;
              pendingOutlineFilterOptionsRef.current = null;
              setOutlineFilterOptions(pending);
            }
          },
        };

        if (col.key === 'course_name' || col.key === 'course_code') {
          column.filterDropdown = textSearchFilterDropdown(
            col.key === 'course_name' ? '搜索课程名称' : '搜索课程代码'
          );
          column.filterIcon = filtered => (
            <SearchOutlined style={{ color: filtered ? 'var(--color-brand)' : undefined }} />
          );
          column.onFilter = (value, record) => includesText(record[col.key], value);
        }

        if (col.key === 'credit') {
          column.filterDropdown = numericRangeFilterDropdown('最小学分', '最大学分');
          column.onFilter = (value, record) => matchesNumericRange(value, record.credit);
        }

        if (col.key === 'course_name') {
          column.render = (text, record) => (
            <div className="academic-course-identity">
              <div className="course-name">
                <Button type="link" size="small" className="academic-outline-link" onClick={() => setOutlineCourse(record)}>
                  {text}
                </Button>
              </div>
              <div className="course-code">{record.course_code || '无课程代码'}</div>
            </div>
          );
        }

        if (col.key === 'assessment_method' || col.key === 'grading_scale') {
          column.filters = outlineFilterOptions[col.key] || [];
          column.filterSearch = true;
          column.onFilter = (value, record) => {
            const metadata = outlineMetadata[record.course_code];
            const current = metadata?.[col.key]
              || (col.key === 'assessment_method' ? record.exam_type : '');
            return current === value;
          };
          column.render = (_text, record) => {
            const metadata = outlineMetadata[record.course_code];
            const value = metadata?.[col.key] || (col.key === 'assessment_method' ? record.exam_type : '');
            if (metadata?.status === 'not_found') return <Text type="secondary">无大纲</Text>;
            if (outlineSyncing && !metadata) return <Text type="secondary">加载中…</Text>;
            return value || <Text type="secondary">-</Text>;
          };
        }

        if (col.key === 'status') {
          column.render = (text, record) => (
            <StatusTag 
              status={text} 
              isPassed={record.is_passed} 
              isSelected={record.is_selected}
              isPlanned={record.is_planned}
            />
          );
          column.filters = [
            { text: '已通过', value: 'passed' },
            { text: '已选课', value: 'selected' },
            { text: '未修读', value: 'planned' },
          ];
          column.onFilter = (value, record) => {
            return courseStatusValue(record) === value;
          };
        }

        if (col.key === 'is_passed') {
          column.render = (text, record) => (
            record.is_passed ? 
              <Tag color="success" icon={<CheckOutlined />}>是</Tag> : 
              <Tag color="default">否</Tag>
          );
          column.filters = [
            { text: '已通过', value: true },
            { text: '未通过', value: false },
          ];
          column.onFilter = (value, record) => {
            return record.is_passed === value;
          };
        }

        if (col.key === 'score') {
          column.filterDropdown = textSearchFilterDropdown('搜索成绩');
          column.filterIcon = filtered => (
            <SearchOutlined style={{ color: filtered ? 'var(--color-brand)' : undefined }} />
          );
          column.onFilter = (value, record) => includesText(record.score, value);
          column.render = (text, record) => {
            if (!text) return '-';
            
            // 成绩颜色完全按照绩点显示：
            // 绩点 3.5-5.0: 绿色
            // 绩点 2.5-3.5: 青色
            // 绩点 1.0-2.5: 蓝色
            // 绩点 <1.0: 红色
            
            const gpa = parseFloat(record.gpa);
            let color = 'default';
            
            if (!isNaN(gpa)) {
              if (gpa >= 3.5) {
                color = 'success';      // 绿色
              } else if (gpa >= 2.5) {
                color = 'cyan';         // 青色
              } else if (gpa >= 1.0) {
                color = 'blue';         // 蓝色
              } else {
                color = 'error';        // 红色
              }
            }
            
            return <Tag color={color}>{text}</Tag>;
          };
        }

        if (col.key === 'credit') {
          column.render = (text) => (
            <span className="course-credit-value">{text ?? '-'}</span>
          );
        }

        if (col.key === 'is_core') {
          column.render = (text) => text ? <Tag color="red">核心</Tag> : '-';
        }

        // 性质列：添加筛选功能
        if (col.key === 'course_nature') {
          column.filters = uniqueFilterOptions(allCourses.map(course => course.course_nature));
          column.filterSearch = true;
          column.onFilter = (value, record) => {
            return record.course_nature === value;
          };
        }

        if (col.key === 'category_path') {
          column.filterDropdown = textSearchFilterDropdown('搜索类别路径');
          column.filterIcon = filtered => (
            <SearchOutlined style={{ color: filtered ? 'var(--color-brand)' : undefined }} />
          );
          column.onFilter = (value, record) => includesText(
            categoryPathText(record.category_path),
            value,
          );
          column.render = (text) => {
            const pathStr = Array.isArray(text) ? text.join(' > ') : (text || '-');
            return <span className="academic-category-path" title={pathStr}>{pathStr}</span>;
          };
        }

        // 学期列：转换学期代码为中文学期
        if (col.key === 'term_code') {
          column.filters = academicTermFilterOptions(
            allCourses.map(course => course.term_code)
          ).map(option => ({ ...option, text: formatTermCode(option.value) }));
          column.filterSearch = true;
          column.onFilter = (value, record) => record.term_code === value;
          column.render = (text) => formatTermCode(text);
        }

        if (col.key === 'is_core') {
          column.filters = [
            { text: '核心课', value: true },
            { text: '非核心课', value: false },
          ];
          column.onFilter = (value, record) => Boolean(record.is_core) === value;
        }

        return column;
      });
  }, [allCourses, columnConfig, outlineFilterOptions, outlineMetadata, outlineSyncing]);

  const preferredTableWidth = useMemo(
    () => tableColumns.reduce((total, column) => total + (Number(column.width) || 100), 0),
    [tableColumns],
  );
  const preferredPageWidth = Math.max(1440, preferredTableWidth + 448);

  // 列选择菜单
  const columnMenuItems = [
    ...columnConfig.map(col => ({
      key: col.key,
      className: 'column-settings-menu-item',
      onClick: () => toggleColumn(col.key),
      label: (
        <Checkbox
          className="column-setting-toggle"
          checked={col.visible}
          tabIndex={-1}
        >
          {col.title}
        </Checkbox>
      ),
    })),
    ...(outlineColumnsEnabled ? [{
      key: 'outline-sync-status',
      disabled: true,
      label: (
        <Text type="secondary">
          {outlineSyncing
            ? `大纲元数据 ${outlineSyncStatus?.completed || 0}/${outlineSyncStatus?.total || allCourses.length}`
            : `大纲元数据已就绪${outlineSyncStatus?.failed ? `，${outlineSyncStatus.failed} 项失败` : ''}`}
        </Text>
      ),
    }, ...(outlineSyncStatus?.failed ? [{
      key: 'outline-sync-retry',
      label: <Button type="link" size="small" onClick={() => {
        return retryOutlineMetadata();
      }}>重试失败项</Button>,
    }] : [])] : []),
    { type: 'divider' },
    {
      key: 'reset',
      className: 'column-settings-menu-item column-settings-reset-item',
      onClick: resetColumnConfig,
      label: '恢复默认',
    },
  ];

  // 刷新按钮文本
  const refreshButtonText = useMemo(() => {
    if (offlineMode) return '只读离线数据';
    const lastUpdate = dataInfo.last_update ? dayjs(dataInfo.last_update) : null;
    if (dataInfo.source === 'remote' || dataInfo.is_fresh) {
      return '已是最新';
    }
    if (lastUpdate) {
      return `本地数据 · ${lastUpdate.format('MM-DD')}`;
    }
    return '刷新';
  }, [dataInfo, offlineMode]);

  // 学分统计
  const creditSummary = report?.credit_summary || {};
  
  // 统计当前显示的课程
  const stats = useMemo(() => {
    const courses = filteredCourses;
    return {
      total: courses.length,
      passed: courses.filter(c => c.is_passed).length,
      selected: courses.filter(c => c.is_selected).length,
      planned: courses.filter(c => c.is_planned).length,
      totalCredits: courses.reduce((sum, c) => sum + (c.credit || 0), 0),
    };
  }, [filteredCourses]);

  const mobileFocusedCourses = useMemo(() => {
    let courses = filteredCourses;
    if (mobilePlanView === 'selected') courses = courses.filter(course => course.is_selected);
    if (mobilePlanView === 'pending') courses = courses.filter(course => !course.is_passed);

    return [...courses].sort((a, b) => {
      if (Boolean(a.is_selected) !== Boolean(b.is_selected)) return a.is_selected ? -1 : 1;
      if ((a.course_nature === '必修') !== (b.course_nature === '必修')) {
        return a.course_nature === '必修' ? -1 : 1;
      }
      return compareAcademicTermsNewestFirst(a.term_code, b.term_code);
    });
  }, [filteredCourses, mobilePlanView]);

  const mobileCourses = useMemo(() => {
    const start = (mobilePage - 1) * 10;
    return mobileFocusedCourses.slice(start, start + 10);
  }, [mobileFocusedCourses, mobilePage]);

  const handleMobilePageChange = (current) => {
    setMobilePage(current);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // 计算整个树的选修/必修统计（用于左侧栏）
  const electiveStats = useMemo(() => {
    const incompleteCategories = findElectiveLeafCategories(categories);
    const totalRemaining = incompleteCategories.reduce((sum, cat) => sum + (cat.remaining_credits || 0), 0);
    return {
      categories: incompleteCategories,
      totalRemaining
    };
  }, [categories]);

  const requiredStats = useMemo(() => {
    const incompleteCategories = findRequiredLeafCategories(categories);
    const totalRemaining = incompleteCategories.reduce((sum, cat) => sum + (cat.remaining_credits || 0), 0);
    return {
      categories: incompleteCategories,
      totalRemaining
    };
  }, [categories]);

  // 获取总计差学分项数
  const totalIncompleteCount = electiveStats.categories.length + requiredStats.categories.length;

  // 计算当前选中层级下的选修/必修还差学分（用于右侧统计栏）
  const currentLevelStats = useMemo(() => {
    // 如果没有选中任何类别，返回总的统计
    if (selectedKeys.length === 0) {
      return {
        electiveRemaining: electiveStats.totalRemaining,
        requiredRemaining: requiredStats.totalRemaining
      };
    }
    
    // 找到当前选中的节点
    const selectedNode = findNodeByWid(categories, selectedKeys[0]);
    if (!selectedNode) {
      return {
        electiveRemaining: electiveStats.totalRemaining,
        requiredRemaining: requiredStats.totalRemaining
      };
    }
    
    // 与全局统计复用同一规则，包含双重约束父组的非重复总量差额。
    const electiveRemaining = calcElectiveRemainingCredits([selectedNode]);
    const requiredRemaining = calcRequiredRemainingCredits([selectedNode]);
    
    return {
      electiveRemaining,
      requiredRemaining
    };
  }, [categories, selectedKeys, electiveStats.totalRemaining, requiredStats.totalRemaining]);

  // 树形数据
  const treeData = useMemo(() => {
    return buildTreeData(categories, expandedKeys);
  }, [categories, expandedKeys]);

  // 数据未加载时显示骨架屏
  if (!dataLoaded) {
    return (
      <div className="academic-report-page">
        <div className="main-content-wrapper" style={{ height: 'auto' }}>
          <Row gutter={[16, 16]} className="main-content">
            <Col span={24} style={{ padding: 8 }}><Card loading style={{ minHeight: 400 }} /></Col>
          </Row>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="empty-container">
        <Empty description="暂无培养计划数据" />
        <Button type="primary" onClick={() => loadData()} style={{ marginTop: 16 }}>
          重新加载
        </Button>
      </div>
    );
  }

  return (
    <div
      className="academic-report-page"
      style={{ '--academic-page-target-width': `${preferredPageWidth}px` }}
    >
      {headerPortalTarget && createPortal(
        <div
          className={`credit-summary-float ${summaryExpanded ? 'expanded' : ''}`}
        >
          <button
            type="button"
            className="float-hint"
            aria-expanded={summaryExpanded}
            aria-controls="credit-summary-panel"
            onClick={() => setSummaryExpanded(expanded => !expanded)}
          >
            <DownOutlined />
            <span className="summary-title">学分统计</span>
            <span className="summary-detail">
              已修 {creditSummary.total_passed || 0}
              <i>/</i>
              要求 {creditSummary.total_required || 0}
              <b>还差 {creditSummary.total_remaining || 0}</b>
            </span>
            <span className="summary-compact">
              {creditSummary.total_passed || 0}/{creditSummary.total_required || 0}
            </span>
          </button>
          <div
            id="credit-summary-panel"
            className="float-content"
            role="region"
            aria-label="学分统计详情"
          >
            <Row gutter={[24, 24]} align="middle">
              <Col xs={24} sm={6} md={4}>
                <div className="credit-progress">
                  <Progress
                    type="circle"
                    percent={creditSummary.completion_rate || 0}
                    size={80}
                    strokeColor={{ '0%': '#108ee9', '100%': '#87d068' }}
                    format={(percent) => (
                      <div className="progress-text">
                        <div className="percent">{percent}%</div>
                      </div>
                    )}
                  />
                </div>
              </Col>
              <Col xs={24} sm={18} md={20}>
                <Row gutter={[16, 16]}>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="要求学分"
                      value={creditSummary.total_required || 0}
                      suffix="学分"
                      valueStyle={{ fontSize: '16px' }}
                    />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="已修学分"
                      value={creditSummary.total_passed || 0}
                      suffix="学分"
                      valueStyle={{ color: '#52c41a', fontSize: '16px' }}
                      prefix={<CheckCircleOutlined />}
                    />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="已选学分"
                      value={creditSummary.total_selected || 0}
                      suffix="学分"
                      valueStyle={{ color: '#1890ff', fontSize: '16px' }}
                      prefix={<ClockCircleOutlined />}
                    />
                  </Col>
                  <Col xs={12} sm={6}>
                    <Statistic
                      title="还差学分"
                      value={creditSummary.total_remaining || 0}
                      suffix="学分"
                      valueStyle={{ color: creditSummary.total_remaining > 0 ? '#faad14' : '#52c41a', fontSize: '16px' }}
                      prefix={<ExclamationCircleOutlined />}
                    />
                  </Col>
                </Row>
                <div className="credit-progress-bar" style={{ marginTop: 8 }}>
                  <Progress
                    percent={creditSummary.completion_rate || 0}
                    strokeColor="#52c41a"
                    showInfo={false}
                    size="small"
                  />
                </div>
              </Col>
            </Row>
          </div>
        </div>,
        headerPortalTarget
      )}

      {/* 主内容区域 */}
      <div className="main-content-wrapper">
        <Drawer
          title="选择课程类别"
          placement="left"
          width={Math.min(360, typeof window === 'undefined' ? 360 : window.innerWidth * 0.9)}
          open={mobileCategoryOpen}
          onClose={() => setMobileCategoryOpen(false)}
          className="mobile-category-drawer"
        >
          {totalIncompleteCount > 0 && (
            <Alert
              message={`仍有 ${totalIncompleteCount} 个类别存在学分缺口`}
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
            />
          )}
          <Button
            type={selectedKeys.length === 0 ? 'primary' : 'default'}
            block
            onClick={() => {
              setSelectedKeys([]);
              setDisplayCourses(allCourses);
              setPagination(prev => ({ ...prev, current: 1 }));
              setMobilePage(1);
              setMobileCategoryOpen(false);
            }}
            style={{ marginBottom: 12 }}
          >
            查看全部课程
          </Button>
          <Tree
            showIcon
            onSelect={onSelect}
            onExpand={onExpand}
            selectedKeys={selectedKeys}
            expandedKeys={expandedKeys}
            autoExpandParent={autoExpandParent}
            treeData={treeData}
            className="academic-tree"
            blockNode
          />
        </Drawer>
        <Row gutter={[16, 16]} className="main-content">
          {/* 左侧：类别导航树 */}
          <Col xs={24} lg={8} xl={7} className="tree-container-wrapper">
            <Card 
              className="category-tree-card"
              title={
                <Space>
                  <FolderOutlined />
                  <span>课程类别</span>
                  {totalIncompleteCount > 0 && (
                    <Tag color="red">{totalIncompleteCount} 项差学分</Tag>
                  )}
                </Space>
              }
            >
            {/* 必修课差学分提醒 */}
            {requiredStats.categories.length > 0 && (
              <Alert
                message={
                  <span>
                    {requiredStats.totalRemaining > 0 ? (
                      <>必修课还差 <Text strong style={{ color: '#f5222d' }}>{requiredStats.totalRemaining}</Text> 学分</>
                    ) : '必修培养规则尚未满足'}
                  </span>
                }
                description={
                  <div className="incomplete-list">
                    {requiredStats.categories.map(cat => (
                      <div 
                        key={cat.wid} 
                        className="incomplete-item"
                        onClick={() => {
                          setSelectedKeys([cat.wid]);
                          const courses = filterCoursesByPath(categories, cat.path);
                          setDisplayCourses(courses);
                          // 展开到该节点
                          const parentKeys = cat.path_array.map((_, idx) => {
                            const path = cat.path_array.slice(0, idx + 1).join(' > ');
                            const findNode = (nodes) => {
                              for (const n of nodes) {
                                if (n.path === path) return n.wid;
                                if (n.children) {
                                  const found = findNode(n.children);
                                  if (found) return found;
                                }
                              }
                              return null;
                            };
                            return findNode(categories);
                          }).filter(Boolean);
                          setExpandedKeys([...new Set([...expandedKeys, ...parentKeys])]);
                        }}
                      >
                        <span className="name">{cat.name}</span>
                        <span className="credits" style={{ color: '#f5222d' }}>
                          {getAcademicRuleDeficitText(cat)}
                        </span>
                      </div>
                    ))}
                  </div>
                }
                type="error"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}

            {/* 选修课差学分提醒 */}
            {electiveStats.categories.length > 0 && (
              <Alert
                message={
                  <span>
                    {electiveStats.totalRemaining > 0 ? (
                      <>选修课还差 <Text strong style={{ color: '#faad14' }}>{electiveStats.totalRemaining}</Text> 学分</>
                    ) : '选修培养规则尚未满足'}
                  </span>
                }
                description={
                  <div className="incomplete-list">
                    {electiveStats.categories.map(cat => (
                      <div 
                        key={cat.wid} 
                        className="incomplete-item"
                        onClick={() => {
                          setSelectedKeys([cat.wid]);
                          const courses = filterCoursesByPath(categories, cat.path);
                          setDisplayCourses(courses);
                          // 展开到该节点
                          const parentKeys = cat.path_array.map((_, idx) => {
                            const path = cat.path_array.slice(0, idx + 1).join(' > ');
                            const findNode = (nodes) => {
                              for (const n of nodes) {
                                if (n.path === path) return n.wid;
                                if (n.children) {
                                  const found = findNode(n.children);
                                  if (found) return found;
                                }
                              }
                              return null;
                            };
                            return findNode(categories);
                          }).filter(Boolean);
                          setExpandedKeys([...new Set([...expandedKeys, ...parentKeys])]);
                        }}
                      >
                        <span className="name">{cat.name}</span>
                        <span className="credits" style={{ color: '#f5222d' }}>
                          {getAcademicRuleDeficitText(cat)}
                        </span>
                      </div>
                    ))}

                  </div>
                }
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}

            <div className="tree-scroll-container">
              <Tree
                showIcon
                onSelect={onSelect}
                onExpand={onExpand}
                selectedKeys={selectedKeys}
                expandedKeys={expandedKeys}
                autoExpandParent={autoExpandParent}
                treeData={treeData}
                className="academic-tree"
                blockNode
              />
            </div>
          </Card>
        </Col>

        {/* 右侧：课程列表 */}
        <Col xs={24} lg={16} xl={17} className="table-container-wrapper">
          <Card 
            className="courses-table-card"
            title={
              <Space>
                <ScheduleOutlined />
                <span>课程列表</span>
                <Tag color="blue">{stats.total} 门</Tag>
                {selectedKeys.length > 0 && selectedCategoryName && (
                  <Tag color="green" closable onClose={() => {
                    setSelectedKeys([]);
                    setDisplayCourses(allCourses);
                  }}>
                    {selectedCategoryName}
                  </Tag>
                )}
              </Space>
            }
            extra={
              <Space className={isMobile ? 'academic-mobile-actions' : undefined}>
                <Button
                  type={courseSearchOpen ? 'primary' : 'default'}
                  icon={<SearchOutlined />}
                  onClick={() => {
                    if (courseSearchOpen) {
                      setCourseSearch('');
                      setPagination(previous => ({ ...previous, current: 1 }));
                      setMobilePage(1);
                    }
                    setCourseSearchOpen(open => !open);
                  }}
                >
                  搜索
                </Button>
                {isMobile && (
                  <Button icon={<FolderOutlined />} onClick={() => setMobileCategoryOpen(true)}>
                    类别
                  </Button>
                )}
                {!isMobile && <Dropdown
                  menu={{ items: columnMenuItems }}
                  open={columnMenuOpen}
                  onOpenChange={(open, info) => {
                    if (!open && info?.source === 'menu') return;
                    setColumnMenuOpen(open);
                  }}
                  placement="bottomRight"
                  arrow
                >
                  <Button icon={<SettingOutlined />}>
                    列设置
                  </Button>
                </Dropdown>}
                
                <Tooltip title={offlineMode
                  ? '离线模式不会连接教务系统'
                  : (dataInfo.last_update
                    ? `最后保存: ${dayjs(dataInfo.last_update).format('YYYY-MM-DD HH:mm:ss')}`
                    : '点击刷新云端数据')}>
                  <Button
                    icon={offlineMode ? <DatabaseOutlined /> : <ReloadOutlined />}
                    loading={refreshing}
                    onClick={handleRefresh}
                  disabled={offlineMode}
                  >
                    {isMobile ? (offlineMode ? '离线' : '刷新') : refreshButtonText}
                  </Button>
                </Tooltip>
              </Space>
            }
          >
            {courseSearchOpen && (
              <div className="academic-course-search">
                <Input
                  allowClear
                  autoFocus
                  prefix={<SearchOutlined />}
                  placeholder="搜索课程名称、代码、性质或学期"
                  value={courseSearch}
                  onChange={event => {
                    setCourseSearch(event.target.value);
                    setPagination(previous => ({ ...previous, current: 1 }));
                    setMobilePage(1);
                  }}
                />
                <Text type="secondary">
                  找到 {filteredCourses.length} 门课程
                </Text>
              </div>
            )}
            {/* 统计信息 */}
            <div className="course-stats-bar" style={{ marginBottom: 16, flexShrink: 0 }}>
              {isMobile ? (
                <div className="mobile-plan-summary">
                  <span>
                    <small>待完成</small>
                    <b>{stats.total - stats.passed} 门</b>
                  </span>
                  <span>
                    <small>已选课程</small>
                    <b>{stats.selected} 门</b>
                  </span>
                  <span>
                    <small>还差学分</small>
                    <b>{(currentLevelStats.requiredRemaining + currentLevelStats.electiveRemaining).toFixed(2)}</b>
                  </span>
                </div>
              ) : (
                <Space size="large" wrap>
                  <span>
                    <CheckCircleOutlined style={{ color: '#52c41a' }} /> 已通过:
                    <Text strong style={{ color: '#52c41a' }}> {stats.passed}</Text> 门
                  </span>
                  <span>
                    <ClockCircleOutlined style={{ color: '#1890ff' }} /> 已选课:
                    <Text strong style={{ color: '#1890ff' }}> {stats.selected}</Text> 门
                  </span>
                  <span>
                    <BookOutlined style={{ color: '#8c8c8c' }} /> 未修读:
                    <Text strong> {stats.planned}</Text> 门
                  </span>
                  <span>
                    <TrophyOutlined style={{ color: '#faad14' }} /> 总学分:
                    <Text strong> {stats.totalCredits.toFixed(2)}</Text> 学分
                  </span>
                  {currentLevelStats.requiredRemaining > 0 && (
                    <span>
                      <SafetyOutlined style={{ color: '#ff4d4f' }} /> 必修还差:
                      <Text strong style={{ color: '#ff4d4f' }}> {currentLevelStats.requiredRemaining}</Text> 学分
                    </span>
                  )}
                  {currentLevelStats.electiveRemaining > 0 && (
                    <span>
                      <ExclamationCircleOutlined style={{ color: '#faad14' }} /> 选修还差:
                      <Text strong style={{ color: '#faad14' }}> {currentLevelStats.electiveRemaining}</Text> 学分
                    </span>
                  )}
                </Space>
              )}
            </div>

            {isMobile ? (
              <>
                <div className="mobile-focus-toolbar academic-focus-toolbar">
                  <div>
                    <strong>当前关注</strong>
                    <span>{selectedCategoryName || '全部课程类别'}</span>
                  </div>
                  <Segmented
                    size="small"
                    value={mobilePlanView}
                    options={[
                      { label: '待完成', value: 'pending' },
                      { label: '已选', value: 'selected' },
                      { label: '全部', value: 'all' },
                    ]}
                    onChange={value => {
                      setMobilePlanView(value);
                      setMobilePage(1);
                    }}
                  />
                </div>
                <div className="academic-mobile-list" aria-label="培养计划课程列表">
                  {mobileCourses.map(course => (
                    <article
                      className="academic-mobile-course is-interactive"
                      key={course._id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setMobileCourseDetail(course)}
                      onKeyDown={event => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          setMobileCourseDetail(course);
                        }
                      }}
                    >
                      <div className="academic-mobile-course__header">
                        <div>
                          <strong>{course.course_name}</strong>
                        </div>
                        <StatusTag
                          status={course.status}
                          isPassed={course.is_passed}
                          isSelected={course.is_selected}
                          isPlanned={course.is_planned}
                        />
                      </div>
                      <div className="academic-mobile-course__details">
                        <span><small>学分</small><b>{course.credit ?? '-'}</b></span>
                        <span><small>性质</small><b>{course.course_nature || '-'}</b></span>
                        <span><small>学期</small><b>{formatTermCode(course.term_code)}</b></span>
                      </div>
                    </article>
                  ))}
                  {mobileCourses.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有需要关注的课程" />}
                </div>
                <Pagination
                  className="mobile-list-pagination"
                  simple
                  current={mobilePage}
                  pageSize={10}
                  total={mobileFocusedCourses.length}
                  onChange={handleMobilePageChange}
                />
              </>
            ) : (
              <div
                className="table-scroll-container"
                style={{ '--academic-table-content-width': `${preferredTableWidth}px` }}
              >
                <Table
                  columns={tableColumns}
                  dataSource={orderedFilteredCourses}
                  rowKey="_id"
                  pagination={pagination}
                  onChange={handleTableChange}
                  scroll={{ x: preferredTableWidth }}
                  tableLayout="fixed"
                  bordered={false}
                  size="middle"
                  className="data-table"
                />
              </div>
            )}
          </Card>
        </Col>
      </Row>
      </div>
      
      <MobileDetailDrawer
        open={Boolean(mobileCourseDetail)}
        onClose={() => setMobileCourseDetail(null)}
        title={mobileCourseDetail?.course_name || '培养计划课程详情'}
      >
        {mobileCourseDetail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="课程代码">{mobileCourseDetail.course_code || '-'}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <StatusTag
                status={mobileCourseDetail.status}
                isPassed={mobileCourseDetail.is_passed}
                isSelected={mobileCourseDetail.is_selected}
                isPlanned={mobileCourseDetail.is_planned}
              />
            </Descriptions.Item>
            <Descriptions.Item label="成绩">{mobileCourseDetail.score ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="学分">{mobileCourseDetail.credit ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="课程性质">{mobileCourseDetail.course_nature || '-'}</Descriptions.Item>
            <Descriptions.Item label="计划学期">{formatTermCode(mobileCourseDetail.term_code)}</Descriptions.Item>
            <Descriptions.Item label="类别路径">{mobileCourseDetail.category_path || '-'}</Descriptions.Item>
            <Descriptions.Item label="核心课程">{mobileCourseDetail.is_core ? '是' : '否'}</Descriptions.Item>
            <Descriptions.Item label="课程大纲">
              <Button size="small" type="link" onClick={() => setOutlineCourse(mobileCourseDetail)}>查看课程大纲</Button>
            </Descriptions.Item>
          </Descriptions>
        )}
      </MobileDetailDrawer>
      <CourseOutlineDrawer open={Boolean(outlineCourse)} course={outlineCourse} onClose={() => setOutlineCourse(null)} />
      <Modal
        title="培养计划已有更新"
        open={Boolean(pendingPlanUpdate)}
        okText="刷新当前显示"
        cancelText="稍后"
        maskClosable={false}
        keyboard={false}
        closable={false}
        onOk={applyPendingPlanUpdate}
        onCancel={() => setPendingPlanUpdate(null)}
      >
        <ResourceUpdateSummary items={pendingPlanUpdate?.summary || []} />
        <Text type="secondary">
          刷新后，你的搜索、分类和展开状态会尽量保留。
        </Text>
      </Modal>
    </div>
  );
};

export {
  findLeafCategories,
  calcElectiveRemainingCredits,
  calcRequiredRemainingCredits
};
export default AcademicReportPage;
