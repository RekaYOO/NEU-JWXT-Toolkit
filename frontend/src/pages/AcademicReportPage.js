import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Table, Card, Statistic, Row, Col, Button, Tag, message, Alert,
  Tooltip, Dropdown, Checkbox, Space, InputNumber, Typography, Progress,
  Tree, Badge, Empty, Divider, Switch
} from 'antd';
import {
  ReloadOutlined, BookOutlined, CheckCircleOutlined, TrophyOutlined,
  SettingOutlined, DatabaseOutlined, CloudSyncOutlined, ScheduleOutlined,
  SafetyOutlined, ClockCircleOutlined, CheckOutlined, DownOutlined,
  RightOutlined, FolderOutlined, FileOutlined, PercentageOutlined,
  ExclamationCircleOutlined, FilterOutlined, DownCircleOutlined, UpCircleOutlined,
  CheckSquareOutlined
} from '@ant-design/icons';
import { getAcademicReport, getAcademicReportSummary, refreshAcademicReport, cancelRequest } from '../services/api';
import { columnSettings } from '../utils/settings';
import dayjs from 'dayjs';
import './AcademicReportPage.css';

const { Title, Text } = Typography;

// 列配置 - 添加is_passed列（默认隐藏）
const DEFAULT_COLUMNS = [
  { key: 'course_name', title: '课程名称', visible: true, width: 240 },
  { key: 'course_code', title: '课程代码', visible: true, width: 120 },
  { key: 'credit', title: '学分', visible: true, width: 70 },
  { key: 'status', title: '状态', visible: true, width: 100 },
  { key: 'score', title: '成绩', visible: true, width: 80 },
  { key: 'course_nature', title: '性质', visible: true, width: 80 },
  { key: 'is_passed', title: '通过', visible: false, width: 80 },
  { key: 'category_path', title: '类别路径', visible: false, width: 200 },
  { key: 'term_code', title: '学期', visible: false, width: 130 },
  { key: 'is_core', title: '核心课', visible: false, width: 80 },
];

const getDefaultColumns = () => JSON.parse(JSON.stringify(DEFAULT_COLUMNS));

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
      const earnedCredits = node.earned_credits ?? 0;
      const takenCredits = node.taken_credits ?? 0;
      const requiredCredits = node.required_credits ?? 0;
      // 已修学分 = 已通过学分 + 已选学分（已选课也算已修）
      const totalEarned = earnedCredits + takenCredits;
      const isCompleted = node.is_completed ?? (totalEarned >= requiredCredits);
      const hasDeficit = requiredCredits > totalEarned;
      
      // 计算课程数量
      const courseCount = countCourses(node);
      
      const title = (
        <div className="tree-node-title">
          <span className="node-name" style={{ color }}>
            {node.name}
          </span>
          <span className="node-credits">
            {requiredCredits > 0 && (
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

// 判断类别是否是选修类（直接选修或包含选修子类别）
const isElectiveCategory = (node) => {
  if (!node.path_array || node.path_array.length === 0) return false;
  const pathStr = node.path_array.join(' > ');
  // 直接属于选修类
  if (node.name === '选修') return true;
  // 路径中包含选修
  if (pathStr.includes('选修')) return true;
  return false;
};

// 判断类别是否是必修类
const isRequiredCategory = (node) => {
  if (!node.path_array || node.path_array.length === 0) return false;
  const pathStr = node.path_array.join(' > ');
  // 直接属于必修类
  if (node.name === '必修') return true;
  // 路径中包含必修
  if (pathStr.includes('必修')) return true;
  // 实践类及其子类也算作必修
  if (node.name === '实践类' || pathStr.includes('实践类')) return true;
  return false;
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
  const result = [];
  
  const traverse = (nodes, parentNode = null) => {
    nodes.forEach(node => {
      if (!filterFn(node)) {
        // 不是目标类别，继续遍历子节点（传递当前节点作为父节点）
        if (node.children) {
          traverse(node.children, node);
        }
        return;
      }
      
      // 是目标类别
      const hasChildrenWithCredits = node.children && node.children.some(child => 
        child.required_credits > 0
      );
      
      // 如果当前节点要求学分>0
      if (node.required_credits > 0) {
        // 检查子节点是否都学分为0
        const childrenAllZero = node.children && node.children.every(child => 
          child.required_credits === 0 && (!child.children || child.children.length === 0)
        );
        
        // 如果没有子节点，或者子节点都学分为0，则统计当前节点
        // 只要有差额（remaining_credits > 0）就显示，不管是否标记为已完成
        if (!node.children || node.children.length === 0 || childrenAllZero) {
          if (node.remaining_credits > 0) {
            result.push({
              wid: node.wid,
              name: getCategoryDisplayName(node),
              originalName: node.name,
              path: node.path,
              path_array: node.path_array,
              required_credits: node.required_credits,
              earned_credits: node.earned_credits,
              remaining_credits: node.remaining_credits,
              is_completed: node.is_completed
            });
          }
        }
      }
      
      // 继续遍历子节点（传递当前节点作为父节点）
      if (node.children) {
        traverse(node.children, node);
      }
    });
  };
  
  traverse(categories);
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

const AcademicReportPage = () => {
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
  
  // 表格筛选状态
  
  // 缺学分列表展开状态
  const [showAllIncomplete, setShowAllIncomplete] = useState(false);
  
  // 悬挂式学分统计卡片展开状态
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  
  // 列配置
  const [columnConfig, setColumnConfig] = useState(() => {
    const saved = columnSettings.load(getDefaultColumns(), 'academicReportColumnConfig');
    return saved;
  });
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  
  // 分页
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 20,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (total) => `共 ${total} 门课程`,
  });

  // 加载数据
  const loadData = async () => {
    try {
      const reportData = await getAcademicReport(false);
      
      setReport(reportData);
      setCategories(reportData.categories || []);
      
      const all = collectAllCourses(reportData.categories || []);
      setAllCourses(all);
      setDisplayCourses(all);
      
      setDataInfo({
        source: reportData.source,
        is_fresh: reportData.is_fresh,
        last_update: reportData.last_update,
      });
      
      // 默认展开第一层
      const firstLevelKeys = (reportData.categories || []).map(c => c.wid);
      setExpandedKeys(firstLevelKeys);
      
      setDataLoaded(true);
    } catch (error) {
      // 如果是请求取消，不显示错误
      if (error.name === 'CanceledError' || error.name === 'AbortError') {
        console.log('[AcademicReport] 请求已取消');
        return;
      }
      console.error('加载培养计划失败:', error);
      message.error('加载培养计划失败');
      setDataLoaded(true);
    }
  };

  useEffect(() => {
    loadData();
    
    // 组件卸载时取消未完成的请求
    return () => {
      cancelRequest('academicReport');
    };
  }, []);

  // 刷新数据
  const handleRefresh = async () => {
    setRefreshing(true);
    message.loading('正在刷新...', 0);
    
    try {
      await refreshAcademicReport();
      await loadData();
      message.destroy();
      message.success('数据已刷新');
    } catch (error) {
      message.destroy();
      message.error('刷新失败: ' + error.message);
    } finally {
      setRefreshing(false);
    }
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
    return [...displayCourses];
  }, [displayCourses]);

  // 生成筛选选项
  const getFilterOptions = (key) => {
    const values = [...new Set(allCourses.map(c => c[key]).filter(Boolean))];
    return values.map(v => ({ text: v, value: v }));
  };

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
    return columnConfig
      .filter(col => col.visible)
      .map(col => {
        const column = {
          title: col.title,
          dataIndex: col.key,
          key: col.key,
          width: col.width,
          sorter: (a, b) => {
            if (col.key === 'credit') {
              return (a.credit || 0) - (b.credit || 0);
            }
            return String(a[col.key] || '').localeCompare(String(b[col.key] || ''));
          },
        };

        if (col.key === 'course_name') {
          column.render = (text, record) => (
            <div>
              <div className="course-name">{text}</div>
              <div className="course-code">{record.course_code}</div>
            </div>
          );
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
            if (value === 'passed') return record.is_passed;
            if (value === 'selected') return record.is_selected;
            if (value === 'planned') return record.is_planned;
            return true;
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
          column.render = (text) => {
            if (!text) return '-';
            const score = parseFloat(text);
            let color = 'default';
            if (!isNaN(score)) {
              if (score >= 90) color = 'success';
              else if (score >= 60) color = 'processing';
              else color = 'error';
            }
            return <Tag color={color}>{text}</Tag>;
          };
        }

        if (col.key === 'credit') {
          column.render = (text) => `${text} 学分`;
        }

        if (col.key === 'is_core') {
          column.render = (text) => text ? <Tag color="red">核心</Tag> : '-';
        }

        // 性质列：添加筛选功能
        if (col.key === 'course_nature') {
          column.filters = [
            { text: '必修', value: '必修' },
            { text: '选修', value: '选修' },
          ];
          column.onFilter = (value, record) => {
            return record.course_nature === value;
          };
        }

        if (col.key === 'category_path') {
          column.render = (text) => {
            const pathStr = Array.isArray(text) ? text.join(' > ') : (text || '-');
            return (
              <Text ellipsis style={{ maxWidth: 180 }} title={pathStr}>
                {pathStr}
              </Text>
            );
          };
        }

        // 学期列：转换学期代码为中文学期
        if (col.key === 'term_code') {
          column.render = (text) => formatTermCode(text);
        }

        return column;
      });
  }, [columnConfig]);

  // 列选择菜单
  const columnMenuItems = [
    ...columnConfig.map(col => ({
      key: col.key,
      label: (
        <Checkbox 
          checked={col.visible}
          onChange={() => toggleColumn(col.key)}
        >
          {col.title}
        </Checkbox>
      ),
    })),
    { type: 'divider' },
    {
      key: 'reset',
      label: (
        <Button 
          type="link" 
          size="small" 
          onClick={resetColumnConfig}
          style={{ padding: 0 }}
        >
          恢复默认
        </Button>
      ),
    },
  ];

  // 刷新按钮文本
  const refreshButtonText = useMemo(() => {
    const lastUpdate = dataInfo.last_update ? dayjs(dataInfo.last_update) : null;
    if (dataInfo.source === 'remote' || dataInfo.is_fresh) {
      return '已是最新';
    }
    if (lastUpdate) {
      return `本地数据 · ${lastUpdate.format('MM-DD')}`;
    }
    return '刷新';
  }, [dataInfo]);

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
    
    // 收集当前节点及其所有子节点
    const collectNodes = (node) => {
      const nodes = [node];
      if (node.children) {
        node.children.forEach(child => {
          nodes.push(...collectNodes(child));
        });
      }
      return nodes;
    };
    
    const allNodesInScope = collectNodes(selectedNode);
    
    // 计算选修还差学分 - 只统计叶节点（没有子节点的），避免重复计算
    const electiveNodes = allNodesInScope.filter(node => {
      if (!isElectiveCategory(node)) return false;
      // 只统计叶节点或没有子类别的节点
      const isLeaf = !node.children || node.children.length === 0;
      return isLeaf && (node.remaining_credits || 0) > 0;
    });
    const electiveRemaining = electiveNodes.reduce((sum, node) => sum + (node.remaining_credits || 0), 0);
    
    // 计算必修还差学分 - 只统计叶节点，避免重复计算
    const requiredNodes = allNodesInScope.filter(node => {
      if (!isRequiredCategory(node)) return false;
      const isLeaf = !node.children || node.children.length === 0;
      return isLeaf && (node.remaining_credits || 0) > 0;
    });
    const requiredRemaining = requiredNodes.reduce((sum, node) => sum + (node.remaining_credits || 0), 0);
    
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
    <div className="academic-report-page">
      {/* 悬挂式学分统计卡片 */}
      <div 
        className={`credit-summary-float ${summaryExpanded ? 'expanded' : ''}`}
        onMouseEnter={() => setSummaryExpanded(true)}
        onMouseLeave={() => setSummaryExpanded(false)}
      >
        <div className="float-hint">
          <DownOutlined /> 学分统计: 已修{creditSummary.total_passed || 0} / 要求{creditSummary.total_required || 0} (还差{creditSummary.total_remaining || 0})
        </div>
        <div className="float-content">
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
      </div>

      {/* 主内容区域 */}
      <div className="main-content-wrapper">
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
                    必修课还差 <Text strong style={{ color: '#f5222d' }}>{requiredStats.totalRemaining}</Text> 学分
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
                          差 {cat.remaining_credits} 学分
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
                    选修课还差 <Text strong style={{ color: '#faad14' }}>{electiveStats.totalRemaining}</Text> 学分
                  </span>
                }
                description={
                  <div className="incomplete-list">
                    {(showAllIncomplete ? electiveStats.categories : electiveStats.categories.slice(0, 3)).map(cat => (
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
                          差 {cat.remaining_credits} 学分
                        </span>
                      </div>
                    ))}
                    {electiveStats.categories.length > 3 && (
                      <div 
                        className="more" 
                        onClick={() => setShowAllIncomplete(!showAllIncomplete)}
                        style={{ cursor: 'pointer', color: '#1890ff' }}
                      >
                        {showAllIncomplete ? (
                          <><UpCircleOutlined /> 收起</>
                        ) : (
                          <><DownCircleOutlined /> 展开 {electiveStats.categories.length - 3} 项</>
                        )}
                      </div>
                    )}
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
              <Space>
                <Dropdown
                  menu={{ items: columnMenuItems }}
                  open={columnMenuOpen}
                  onOpenChange={setColumnMenuOpen}
                  placement="bottomRight"
                  arrow
                >
                  <Button icon={<SettingOutlined />}>
                    列设置
                  </Button>
                </Dropdown>
                
                <Tooltip title={dataInfo.last_update ? `最后保存: ${dayjs(dataInfo.last_update).format('YYYY-MM-DD HH:mm:ss')}` : '点击刷新云端数据'}>
                  <Button
                    icon={<ReloadOutlined />}
                    loading={refreshing}
                    onClick={handleRefresh}
                  >
                    {refreshButtonText}
                  </Button>
                </Tooltip>
              </Space>
            }
          >
            {/* 统计信息 */}
            <div className="course-stats-bar" style={{ marginBottom: 16, flexShrink: 0 }}>
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
                {/* 当前层级必修还差学分 */}
                {currentLevelStats.requiredRemaining > 0 && (
                  <span>
                    <SafetyOutlined style={{ color: '#ff4d4f' }} /> 必修还差: 
                    <Text strong style={{ color: '#ff4d4f' }}> {currentLevelStats.requiredRemaining}</Text> 学分
                  </span>
                )}
                {/* 当前层级选修还差学分 */}
                {currentLevelStats.electiveRemaining > 0 && (
                  <span>
                    <ExclamationCircleOutlined style={{ color: '#faad14' }} /> 选修还差: 
                    <Text strong style={{ color: '#faad14' }}> {currentLevelStats.electiveRemaining}</Text> 学分
                  </span>
                )}
              </Space>
            </div>

            <div className="table-scroll-container">
              <Table
                columns={tableColumns}
                dataSource={filteredCourses}
                rowKey="_id"
                pagination={pagination}
                onChange={handleTableChange}
                scroll={{ x: 'max-content' }}
                bordered={false}
                size="middle"
                className="data-table"
              />
            </div>
          </Card>
        </Col>
      </Row>
      </div>
      
      {/* 底部信息 */}
      <div className="footer-info" style={{ marginTop: 16, textAlign: 'center' }}>
        <Text type="secondary">
          <ClockCircleOutlined /> 培养计划更新时间: {report.calculated_time || '-'}
        </Text>
      </div>
    </div>
  );
};

export default AcademicReportPage;
