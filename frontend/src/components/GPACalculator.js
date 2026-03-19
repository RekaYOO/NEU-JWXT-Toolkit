import React, { useState, useEffect, useMemo, useRef, useImperativeHandle, forwardRef } from 'react';
import {
  Table, Card, Statistic, Row, Col, Button, Tag, message,
  Space, InputNumber, Input, Tooltip, Tabs, Popconfirm,
  Badge, Empty, Alert, Modal, Drawer, Spin, Upload, List,
  Typography, Tree, Checkbox, Divider
} from 'antd';
import {
  PlusOutlined, ImportOutlined, ExportOutlined,
  DeleteOutlined, EditOutlined, BookOutlined, TrophyOutlined,
  ReloadOutlined, CheckCircleOutlined, WarningOutlined, CloseOutlined,
  SaveOutlined, CloudUploadOutlined, CloudDownloadOutlined, SearchOutlined,
  FileTextOutlined, FolderOutlined, FileOutlined, FilterOutlined
} from '@ant-design/icons';
import { 
  getAcademicReport,
  exportGPASimulation,
  listGPASimulationFiles,
  getGPASimulationFile,
  deleteGPASimulationFile
} from '../services/api';
import './GPACalculator.css';

const { TabPane } = Tabs;
const { Text } = Typography;

/**
 * GPA计算器组件 - 嵌入式版本（纯本地存储）
 * 
 * 规则：
 * 1. 绩点直接使用系统返回的原始数据
 * 2. 成绩直接使用系统返回的原始数据（数字或文字等级）
 * 3. 总GPA = Σ(绩点×学分)/Σ学分（加权平均）
 * 4. 数据导出到本地文件，从本地文件导入
 */

const GPACalculator = forwardRef(({
  realScores = [],
  onCoursesChange = null,
  onSimulatingChange = null,
}, ref) => {
  // ===== 状态管理 =====
  const [courses, setCourses] = useState([]);
  const [editingKey, setEditingKey] = useState(null);
  const [activeTab, setActiveTab] = useState('all');
  const [isSimulating, setIsSimulating] = useState(false);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  
  // 培养计划导入
  const [importDrawerVisible, setImportDrawerVisible] = useState(false);
  const [academicPlan, setAcademicPlan] = useState([]);
  const [planCategories, setPlanCategories] = useState([]); // 嵌套分类树
  const [planLoading, setPlanLoading] = useState(false);
  const [selectedPlanCourses, setSelectedPlanCourses] = useState([]);
  const [planSearchText, setPlanSearchText] = useState('');
  const [selectedCategoryKey, setSelectedCategoryKey] = useState('all'); // 当前选中的分类
  const [showOnlyDeficit, setShowOnlyDeficit] = useState(false); // 只看差学分
  const [quickFilters, setQuickFilters] = useState([]); // 快速筛选：必修/选修/已选课/未修读
  const [expandedKeys, setExpandedKeys] = useState([]); // 树展开状态
  
  // 冲突检测
  const [conflictModalVisible, setConflictModalVisible] = useState(false);
  const [conflicts, setConflicts] = useState([]);

  // 保存文件Modal
  const [saveModalVisible, setSaveModalVisible] = useState(false);
  const [saveFilename, setSaveFilename] = useState('');

  // 重命名Modal
  const [renameModalVisible, setRenameModalVisible] = useState(false);
  const [renameOldFile, setRenameOldFile] = useState('');
  const [renameNewName, setRenameNewName] = useState('');

  // 导入冲突选择
  const [importConflicts, setImportConflicts] = useState([]);
  const [importConflictModalVisible, setImportConflictModalVisible] = useState(false);
  const [pendingImportedCourses, setPendingImportedCourses] = useState([]);

  // 编辑表单
  const [editForm, setEditForm] = useState({
    name: '',
    code: '',
    credit: 0,
    score: '',
    gpa: 0,
  });

  // 暴露方法
  useImperativeHandle(ref, () => ({
    startSimulation: () => {
      // 重置为原始成绩，清除之前的编辑
      setHistory([]);
      setHistoryIndex(-1);
      setHasUnsavedChanges(false);
      
      // 直接使用真实成绩初始化
      if (realScores.length > 0) {
        const initialized = realScores.map((s, idx) => ({
          key: `real_${s.code}_${idx}`,
          name: s.name,
          code: s.code,
          credit: parseFloat(s.credit) || 0,
          score: s.score,
          gpa: parseFloat(s.gpa) || 0,
          term: s.term_display || s.term,
          courseType: s.course_type,
          isReal: true,
          isCustom: false,
          originalData: { ...s },
        }));
        setCourses(initialized);
        // 保存到历史但不标记为未保存
        const newHistory = [JSON.parse(JSON.stringify(initialized))];
        setHistory(newHistory);
        setHistoryIndex(0);
      } else {
        setCourses([]);
      }
      
      setIsSimulating(true);
    },
    stopSimulation: () => setIsSimulating(false),
    getCourses: () => courses,
    isSimulating: () => isSimulating,
  }));

  // 通知父组件状态变化（仅在内部状态改变时通知，避免循环）
  const prevSimulatingRef = useRef(isSimulating);
  useEffect(() => {
    if (onSimulatingChange && prevSimulatingRef.current !== isSimulating) {
      prevSimulatingRef.current = isSimulating;
      onSimulatingChange(isSimulating);
    }
  }, [isSimulating, onSimulatingChange]);

  // 初始化
  useEffect(() => {
    if (realScores.length > 0 && courses.length === 0) {
      const initialized = realScores.map((s, idx) => ({
        key: `real_${s.code}_${idx}`,
        name: s.name,
        code: s.code,
        credit: parseFloat(s.credit) || 0,
        score: s.score,
        gpa: parseFloat(s.gpa) || 0,
        term: s.term_display || s.term,
        courseType: s.course_type,
        isReal: true,
        isCustom: false,
        originalData: { ...s },
      }));
      setCourses(initialized);
      saveToHistory(initialized, false);
    }
  }, [realScores]);

  // ===== 历史记录 =====
  const saveToHistory = (newCourses, markUnsaved = true) => {
    const newHistory = history.slice(0, historyIndex + 1);
    newHistory.push(JSON.parse(JSON.stringify(newCourses)));
    if (newHistory.length > 20) newHistory.shift();
    setHistory(newHistory);
    setHistoryIndex(newHistory.length - 1);
    if (markUnsaved) setHasUnsavedChanges(true);
    
    if (onCoursesChange) {
      onCoursesChange(newCourses);
    }
  };

  const undo = () => {
    if (historyIndex > 0) {
      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      const restoredCourses = JSON.parse(JSON.stringify(history[newIndex]));
      setCourses(restoredCourses);
      if (onCoursesChange) onCoursesChange(restoredCourses);
      message.success('已撤销');
    }
  };

  // ===== GPA计算 =====
  const stats = useMemo(() => {
    if (!courses.length) return {
      totalCourses: 0,
      totalCredits: 0,
      weightedGPA: 0,
      passedCount: 0,
      realCount: 0,
      customCount: 0,
    };

    const validCourses = courses.filter(c => c.gpa > 0);
    const totalCredits = validCourses.reduce((sum, c) => sum + (c.credit || 0), 0);
    const weightedGPA = totalCredits > 0
      ? validCourses.reduce((sum, c) => sum + (c.gpa || 0) * (c.credit || 0), 0) / totalCredits
      : 0;

    return {
      totalCourses: courses.length,
      totalCredits,
      weightedGPA,
      passedCount: validCourses.filter(c => c.gpa > 0).length,
      realCount: courses.filter(c => c.isReal).length,
      customCount: courses.filter(c => !c.isReal).length,
    };
  }, [courses]);

  // ===== 课程操作 =====
  // 使用 ref 暂存编辑中的值，避免频繁更新状态
  const editingValuesRef = useRef({});
  
  const handleScoreChange = (key, newScore) => {
    const newCourses = courses.map(c => {
      if (c.key !== key) return c;
      // 如果真实课程被修改，变为模拟状态
      const isModified = c.isReal && c.originalData && c.originalData.score !== newScore;
      return { 
        ...c, 
        score: newScore,
        isReal: isModified ? false : c.isReal 
      };
    });
    setCourses(newCourses);
    saveToHistory(newCourses);
  };
  
  // 处理输入框失焦或按回车时才保存
  const handleInputBlur = (key, field, value) => {
    if (field === 'score') {
      handleScoreChange(key, value);
    } else if (field === 'gpa') {
      handleGPAChange(key, value);
    } else if (field === 'credit') {
      handleCreditChange(key, value);
    }
    // 清除暂存值
    delete editingValuesRef.current[key + field];
  };
  
  const handleInputChange = (key, field, value) => {
    // 只暂存，不更新状态
    editingValuesRef.current[key + field] = value;
  };

  const handleGPAChange = (key, newGPA) => {
    const gpa = parseFloat(newGPA) || 0;
    const newCourses = courses.map(c => {
      if (c.key !== key) return c;
      // 如果真实课程的GPA被修改，变为模拟状态
      const isModified = c.isReal && c.originalData && Math.abs((c.originalData.gpa || 0) - gpa) > 0.01;
      return { 
        ...c, 
        gpa,
        isReal: isModified ? false : c.isReal 
      };
    });
    setCourses(newCourses);
    saveToHistory(newCourses);
  };

  const handleCreditChange = (key, newCredit) => {
    const credit = parseFloat(newCredit) || 0;
    const newCourses = courses.map(c => {
      if (c.key !== key) return c;
      // 如果真实课程的学分被修改，变为模拟状态
      const originalCredit = parseFloat(c.originalData?.credit) || 0;
      const isModified = c.isReal && c.originalData && Math.abs(originalCredit - credit) > 0.01;
      return { 
        ...c, 
        credit,
        isReal: isModified ? false : c.isReal 
      };
    });
    setCourses(newCourses);
    saveToHistory(newCourses);
  };

  const deleteCourse = (key) => {
    const newCourses = courses.filter(c => c.key !== key);
    setCourses(newCourses);
    saveToHistory(newCourses);
    message.success('已删除课程');
  };

  // ===== 添加课程 =====
  const addCustomCourse = () => {
    const newCourse = {
      key: `custom_${Date.now()}`,
      name: '新课程',
      code: '',
      credit: 2.0,
      score: '',
      gpa: 0,
      term: '自定义',
      courseType: '自定义',
      isReal: false,
      isCustom: true,
    };
    const newCourses = [...courses, newCourse];
    setCourses(newCourses);
    saveToHistory(newCourses);
    setActiveTab('pending');
    setEditingKey(newCourse.key);
    setEditForm({
      name: newCourse.name,
      code: newCourse.code,
      credit: newCourse.credit,
      score: newCourse.score,
      gpa: newCourse.gpa,
    });
  };

  const saveEdit = (key) => {
    const newCourses = courses.map(c => {
      if (c.key === key) {
        const newCredit = parseFloat(editForm.credit) || 0;
        const newGPA = parseFloat(editForm.gpa) || 0;
        
        // 检查真实课程是否被修改
        let isModified = false;
        if (c.isReal && c.originalData) {
          const origCredit = parseFloat(c.originalData.credit) || 0;
          const origGPA = parseFloat(c.originalData.gpa) || 0;
          if (c.originalData.name !== (editForm.name || c.name) ||
              c.originalData.code !== editForm.code ||
              Math.abs(origCredit - newCredit) > 0.01 ||
              c.originalData.score !== editForm.score ||
              Math.abs(origGPA - newGPA) > 0.01) {
            isModified = true;
          }
        }
        
        return {
          ...c,
          name: editForm.name || c.name,
          code: editForm.code,
          credit: newCredit,
          score: editForm.score,
          gpa: newGPA,
          isReal: isModified ? false : c.isReal,
        };
      }
      return c;
    });
    setCourses(newCourses);
    saveToHistory(newCourses);
    setEditingKey(null);
  };

  const cancelEdit = () => {
    setEditingKey(null);
    setEditForm({ name: '', code: '', credit: 0, score: '', gpa: 0 });
  };

  const startEdit = (record) => {
    setEditingKey(record.key);
    setEditForm({
      name: record.name,
      code: record.code || '',
      credit: record.credit,
      score: record.score,
      gpa: record.gpa,
    });
  };

  // ===== 培养计划导入 =====
  // 类别颜色
  const CATEGORY_COLORS = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2'];

  // 转换学期代码为中文学期
  const formatTermCode = (termCode) => {
    if (!termCode) return '未安排';
    // 匹配格式：2026-2027-1 或 2026-2027-2
    const match = termCode.match(/(\d{4})-(\d{4})-(\d)/);
    if (match) {
      const [, startYear, endYear, term] = match;
      const termName = term === '1' ? '秋季' : '春季';
      return `${startYear}-${endYear}学年${termName}学期`;
    }
    return termCode;
  };

  // 根据wid查找节点
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

  // 判断类别是否是选修类
  const isElectiveCategory = (node) => {
    if (!node.path_array || node.path_array.length === 0) return false;
    const pathStr = node.path_array.join(' > ');
    if (node.name === '选修') return true;
    if (pathStr.includes('通识选修')) return true;
    return false;
  };

  // 判断类别是否是必修类
  const isRequiredCategory = (node) => {
    if (!node.path_array || node.path_array.length === 0) return false;
    if (node.name === '必修' && !node.path_array.join(' > ').includes('通识')) return true;
    return false;
  };

  // 获取显示名称（如果是"选修"或"必修"，则往上取一层）
  const getCategoryDisplayName = (node) => {
    if (node.name !== '选修' && node.name !== '必修') {
      return node.name;
    }
    if (node.path_array && node.path_array.length >= 2) {
      return node.path_array[node.path_array.length - 2];
    }
    return node.name;
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
            category_wid: node.wid,
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
          const collectNodeCourses = (n) => {
            if (n.courses) {
              courses.push(...n.courses.map(c => ({
                ...c,
                category_name: n.name,
                category_path: n.path,
                category_wid: n.wid,
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

  // 构建树形数据（用于导入抽屉）- 与培养计划页面保持一致
  const buildImportTreeData = (categories, expandedKeys = []) => {
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
        
        // 确保数值有效，默认为0
        const earnedCredits = node.earned_credits ?? 0;
        const takenCredits = node.taken_credits ?? 0;
        const requiredCredits = node.required_credits ?? 0;
        // 已修学分 = 已通过学分 + 已选学分（已选课也算已修）
        const totalEarned = earnedCredits + takenCredits;
        const hasDeficit = requiredCredits > totalEarned;
        const remainingCredits = Math.max(0, requiredCredits - totalEarned);
        
        // 计算课程数量
        const courseCount = countCourses(node);
        
        const title = (
          <div className="import-tree-node">
            <span className="node-name" style={{ color }}>{node.name}</span>
            <span className="node-meta">
              {hasDeficit && (
                <Badge count={`差${remainingCredits.toFixed(1)}`} style={{ backgroundColor: '#ff4d4f', fontSize: '10px' }} />
              )}
            </span>
          </div>
        );
        
        const treeNode = {
          title,
          key: node.wid,
          path: node.path,
          isLeaf: (!node.children || node.children.length === 0),
          icon: (!node.children || node.children.length === 0) ? 
            <FileOutlined style={{ color }} /> : <FolderOutlined style={{ color }} />,
          selectable: true,
          data: node,
          hasDeficit,
          remainingCredits,
        };
        
        if (node.children && node.children.length > 0) {
          const childNodes = traverse(node.children, depth + 1);
          // 如果开启了"只看差学分"，过滤掉没有差学分的子节点
          const filteredChildren = showOnlyDeficit 
            ? childNodes.filter(n => n.hasDeficit || (n.children && n.children.some(c => c.hasDeficit)))
            : childNodes;
          if (filteredChildren.length > 0) {
            treeNode.children = filteredChildren;
          }
        }
        
        return treeNode;
      }).filter(node => {
        // 如果开启"只看差学分"，只保留有差学分的节点或有差学分子节点的节点
        if (!showOnlyDeficit) return true;
        return node.hasDeficit || (node.children && node.children.length > 0);
      });
    };
    
    return traverse(categories);
  };

  const loadAcademicPlan = async () => {
    setPlanLoading(true);
    try {
      const data = await getAcademicReport(false);
      if (data?.categories) {
        // 保存嵌套分类结构
        setPlanCategories(data.categories);
        // 同时保存扁平化的课程列表
        const courses = collectAllCourses(data.categories);
        setAcademicPlan(courses);
        // 默认展开第一层
        const firstLevelKeys = data.categories.map(c => c.wid);
        setExpandedKeys(firstLevelKeys);
      }
    } catch (e) {
      message.error('加载培养计划失败');
    } finally {
      setPlanLoading(false);
    }
  };

  const handleOpenImportDrawer = () => {
    if (academicPlan.length === 0) {
      loadAcademicPlan();
    }
    setSelectedCategoryKey('all');
    setPlanSearchText('');
    setQuickFilters([]);
    setShowOnlyDeficit(false);
    setImportDrawerVisible(true);
  };

  const getPlannedCourses = useMemo(() => {
    const realCodes = new Set(realScores.map(s => s.code));
    const currentCodes = new Set(courses.map(c => c.code));
    
    // 根据选择的分类获取课程
    let baseCourses;
    if (selectedCategoryKey === 'all') {
      baseCourses = academicPlan;
    } else {
      // 找到选中的节点
      const node = findNodeByWid(planCategories, selectedCategoryKey);
      if (node) {
        baseCourses = filterCoursesByPath(planCategories, node.path);
      } else {
        baseCourses = academicPlan;
      }
    }
    
    let filtered = baseCourses.filter(plan => {
      // 排除已存在的课程
      if (realCodes.has(plan.course_code)) return false;
      if (currentCodes.has(plan.course_code)) return false;
      // 排除已通过的课程（有成绩且已通过的）
      // 注意：is_passed 后端返回的是字符串 "是"/"否"
      if (plan.is_passed === '是' || plan.is_passed === true) return false;
      if (plan.status === '通过' || plan.status === '已通过' || plan.status === '合格') return false;
      // 只显示未修或已选课的课程
      return !plan.score || plan.status === '未修读' || plan.status === '未修' || plan.status === '待选' || plan.status === '已选课' || plan.status === '已选';
    });

    // 搜索过滤
    if (planSearchText) {
      const search = planSearchText.toLowerCase();
      filtered = filtered.filter(plan => 
        (plan.course_name && plan.course_name.toLowerCase().includes(search)) ||
        (plan.course_code && plan.course_code.toLowerCase().includes(search))
      );
    }
    
    // 快速筛选过滤
    if (quickFilters.length > 0) {
      filtered = filtered.filter(plan => {
        const nature = plan.course_nature || '';
        const status = plan.status || '未修';
        
        // 检查性质筛选（如果选择了的话）
        const natureFilter = quickFilters.find(f => f === '必修' || f === '选修');
        if (natureFilter) {
          if (natureFilter === '必修' && nature !== '必修') return false;
          if (natureFilter === '选修' && nature !== '选修') return false;
        }
        
        // 检查状态筛选（如果选择了的话）
        const statusFilter = quickFilters.find(f => f === '已选课' || f === '未修读');
        if (statusFilter) {
          if (statusFilter === '已选课' && status !== '已选课' && status !== '已选') return false;
          if (statusFilter === '未修读') {
            // 注意：后端返回的 status 可能是 "未修读"(来自checkCourseVOS)、"未修"或空(来自data)
            const isNotTaken = status === '未修读' || status === '未修' || status === '待选' || !status;
            if (!isNotTaken) return false;
          }
        }
        
        return true;
      });
    }
    
    return filtered.map(plan => ({
      key: `plan_${plan.course_code}`,
      name: plan.course_name,
      code: plan.course_code,
      credit: parseFloat(plan.credit) || 0,
      score: '',
      gpa: 0,
      term: formatTermCode(plan.plan_term || plan.suggest_term || plan.select_term || plan.term_code),
      courseType: plan.course_nature,
      category: plan.category_name || plan.course_category,
      status: plan.status,
      isReal: false,
      isCustom: false,
      fromPlan: true,
      _original: plan,
    }));
  }, [academicPlan, realScores, courses, planSearchText, selectedCategoryKey, quickFilters, planCategories]);

  const importFromPlan = () => {
    if (selectedPlanCourses.length === 0) {
      message.warning('请先选择要导入的课程');
      return;
    }
    
    const planCourses = getPlannedCourses.filter(c => 
      selectedPlanCourses.includes(c.key)
    );
    
    const newCourses = [...courses, ...planCourses];
    setCourses(newCourses);
    saveToHistory(newCourses);
    setImportDrawerVisible(false);
    setSelectedPlanCourses([]);
    setActiveTab('pending');
    message.success(`成功导入 ${planCourses.length} 门课程`);
  };

  // ===== 打开保存对话框 =====
  const handleOpenSaveModal = () => {
    const defaultName = `GPA模拟_${new Date().toISOString().slice(0, 10)}`;
    setSaveFilename(defaultName);
    setSaveModalVisible(true);
  };

  // ===== 执行保存到服务器 =====
  const handleConfirmSave = async () => {
    if (!saveFilename.trim()) {
      message.error('请输入文件名');
      return;
    }
    
    let filename = saveFilename.trim();
    if (!filename.endsWith('.json')) {
      filename += '.json';
    }

    const data = {
      version: '1.0',
      exportTime: new Date().toISOString(),
      stats,
      courses: courses.map(c => ({
        name: c.name,
        code: c.code,
        credit: c.credit,
        score: c.score,
        gpa: c.gpa,
        term: c.term,
        courseType: c.courseType,
        isCustom: c.isCustom,
        isReal: c.isReal,
        originalData: c.originalData,
      })),
    };
    
    try {
      await exportGPASimulation(filename, data);
      setHasUnsavedChanges(false);
      message.success('已保存到服务器');
      setSaveModalVisible(false);
      // 刷新文件列表
      loadServerFiles();
    } catch (e) {
      message.error('保存失败: ' + e.message);
    }
  };

  // ===== 重命名文件 =====
  const handleOpenRenameModal = (filename) => {
    setRenameOldFile(filename);
    setRenameNewName(filename.replace('.json', ''));
    setRenameModalVisible(true);
  };

  const handleConfirmRename = async () => {
    if (!renameNewName.trim()) {
      message.error('请输入新文件名');
      return;
    }
    
    let newFilename = renameNewName.trim();
    if (!newFilename.endsWith('.json')) {
      newFilename += '.json';
    }

    try {
      // 先获取旧文件内容
      const data = await getGPASimulationFile(renameOldFile);
      // 保存为新文件名
      await exportGPASimulation(newFilename, data);
      // 删除旧文件
      await deleteGPASimulationFile(renameOldFile);
      
      message.success('重命名成功');
      setRenameModalVisible(false);
      loadServerFiles();
    } catch (e) {
      message.error('重命名失败: ' + e.message);
    }
  };

  // ===== 服务器文件管理 =====
  const [serverFiles, setServerFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [importFileModalVisible, setImportFileModalVisible] = useState(false);

  const loadServerFiles = async () => {
    setFilesLoading(true);
    try {
      const files = await listGPASimulationFiles();
      setServerFiles(files);
    } catch (e) {
      message.error('加载文件列表失败');
    } finally {
      setFilesLoading(false);
    }
  };

  const handleOpenFileModal = () => {
    loadServerFiles();
    setImportFileModalVisible(true);
  };

  const handleImportFromServer = async (filename) => {
    try {
      const data = await getGPASimulationFile(filename);
      if (!data.courses || !Array.isArray(data.courses)) {
        message.error('无效的成绩单文件');
        return;
      }

      const imported = data.courses.map((c, idx) => ({
        key: `imported_${Date.now()}_${idx}`,
        ...c,
        // 保留原始isReal状态（如果没有则默认为模拟课程）
        isReal: c.isReal !== undefined ? c.isReal : false,
        originalData: c.originalData || null,
      }));

      // 检查同名课程冲突（按名称匹配）
      const conflicts = [];
      const currentCourseMap = new Map();
      courses.forEach(c => {
        // 按名称索引，同名可能有多个
        if (!currentCourseMap.has(c.name)) {
          currentCourseMap.set(c.name, []);
        }
        currentCourseMap.get(c.name).push(c);
      });

      imported.forEach(imp => {
        const sameNameCourses = currentCourseMap.get(imp.name) || [];
        sameNameCourses.forEach(existing => {
          // 数据不同才视为冲突
          const isDifferent = Math.abs((imp.gpa || 0) - (existing.gpa || 0)) > 0.01 ||
                              imp.score !== existing.score ||
                              Math.abs((imp.credit || 0) - (existing.credit || 0)) > 0.01;
          if (isDifferent) {
            conflicts.push({
              key: `${imp.key}_${existing.key}`,
              name: imp.name,
              imported: { ...imp, gpa: imp.gpa || 0, credit: imp.credit || 0 },
              existing: { ...existing, gpa: existing.gpa || 0, credit: existing.credit || 0 },
              choice: 'imported', // 默认选择导入的
            });
          }
        });
      });
      
      setPendingImportedCourses(imported);
      
      if (conflicts.length > 0) {
        setImportConflicts(conflicts);
        setImportConflictModalVisible(true);
      } else {
        // 无冲突直接导入
        finalizeImport(imported);
      }
    } catch (err) {
      message.error('导入失败: ' + err.message);
    }
  };

  // 完成导入
  const finalizeImport = (imported) => {
    setCourses(imported);
    saveToHistory(imported);
    setImportFileModalVisible(false);
    setImportConflictModalVisible(false);
    setImportConflicts([]);
    setPendingImportedCourses([]);
    message.success(`成功导入 ${imported.length} 门课程`);
  };

  // 解决导入冲突选择
  const handleConflictChoiceChange = (key, choice) => {
    setImportConflicts(prev => prev.map(c => 
      c.key === key ? { ...c, choice } : c
    ));
  };

  // 批量设置冲突选择
  const setAllConflictChoices = (choice) => {
    setImportConflicts(prev => prev.map(c => ({ ...c, choice })));
  };

  // 确认导入冲突解决
  const confirmImportConflicts = () => {
    // 根据用户选择，替换或保留课程
    const keptExistingKeys = new Set();
    importConflicts.forEach(c => {
      if (c.choice === 'existing') {
        keptExistingKeys.add(c.existing.key);
      }
    });

    // 过滤掉被替换的现有课程
    const existingKept = courses.filter(c => keptExistingKeys.has(c.key));
    
    // 过滤掉与保留的现有课程同名的导入课程
    const existingKeptNames = new Set(existingKept.map(c => c.name));
    const importedFiltered = pendingImportedCourses.filter(imp => {
      // 如果这门导入课程与某个保留的现有课程同名，则不导入
      return !existingKeptNames.has(imp.name);
    });

    const merged = [...importedFiltered, ...existingKept];
    
    setCourses(merged);
    saveToHistory(merged);
    setImportFileModalVisible(false);
    setImportConflictModalVisible(false);
    setImportConflicts([]);
    setPendingImportedCourses([]);
    message.success(`成功导入 ${importedFiltered.length} 门新课程，保留 ${existingKept.length} 门现有课程`);
  };

  const handleDeleteServerFile = async (filename) => {
    try {
      await deleteGPASimulationFile(filename);
      message.success('文件已删除');
      loadServerFiles();
    } catch (e) {
      message.error('删除失败');
    }
  };

  // ===== 保存到浏览器本地 =====
  const saveSimulation = () => {
    localStorage.setItem('neu_toolbox:gpa_simulation', JSON.stringify({
      timestamp: new Date().toISOString(),
      stats,
      courses,
    }));
    setHasUnsavedChanges(false);
    message.success('已保存到浏览器');
  };

  // ===== 冲突解决 =====
  const resolveConflicts = (useReal) => {
    if (useReal) {
      const realMap = new Map(realScores.map(s => [s.code, s]));
      const newCourses = courses.map(c => {
        const real = realMap.get(c.code);
        if (real && conflicts.some(conf => conf.code === c.code)) {
          return {
            ...c,
            score: real.score,
            gpa: parseFloat(real.gpa) || 0,
            isReal: true,
            originalData: { ...real },
          };
        }
        return c;
      });
      setCourses(newCourses);
      saveToHistory(newCourses);
      message.success('已更新为最新成绩');
    }
    setConflictModalVisible(false);
  };

  // ===== 表格列 =====
  const columns = [
    {
      title: '课程',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (text, record) => {
        if (editingKey === record.key && record.isCustom) {
          return (
            <Input
              value={editForm.name}
              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              size="small"
              placeholder="课程名称"
              autoFocus
            />
          );
        }
        return (
          <div>
            <div className="course-name">
              {record.isReal && <Badge status="success" style={{ marginRight: 4 }} />}
              {record.fromPlan && <Badge status="processing" style={{ marginRight: 4 }} />}
              {record.isCustom && <Badge status="warning" style={{ marginRight: 4 }} />}
              {text}
            </div>
            {record.code && <div className="course-code">{record.code}</div>}
          </div>
        );
      },
    },
    {
      title: '学分',
      dataIndex: 'credit',
      key: 'credit',
      width: 80,
      align: 'center',
      sorter: (a, b) => a.credit - b.credit,
      render: (text, record) => {
        if (editingKey === record.key && record.isCustom) {
          return (
            <InputNumber
              value={editForm.credit}
              onChange={(v) => setEditForm({ ...editForm, credit: v })}
              size="small"
              min={0}
              max={20}
              step={0.5}
              style={{ width: 65 }}
            />
          );
        }
        return isSimulating ? (
          <InputNumber
            defaultValue={text}
            onChange={(v) => handleInputChange(record.key, 'credit', v)}
            onBlur={(e) => handleInputBlur(record.key, 'credit', editingValuesRef.current[record.key + 'credit'] ?? text)}
            onPressEnter={(e) => handleInputBlur(record.key, 'credit', editingValuesRef.current[record.key + 'credit'] ?? text)}
            size="small"
            min={0}
            max={20}
            step={0.5}
            style={{ width: 65 }}
            bordered={false}
          />
        ) : (
          <span>{text}</span>
        );
      },
    },
    {
      title: '成绩',
      dataIndex: 'score',
      key: 'score',
      width: 90,
      align: 'center',
      sorter: (a, b) => {
        const aNum = parseFloat(a.score) || 0;
        const bNum = parseFloat(b.score) || 0;
        return aNum - bNum;
      },
      render: (text, record) => {
        if (editingKey === record.key && record.isCustom) {
          return (
            <Input
              value={editForm.score}
              onChange={(e) => setEditForm({ ...editForm, score: e.target.value })}
              size="small"
              style={{ width: 75 }}
              placeholder="成绩"
            />
          );
        }
        
        const numScore = parseFloat(text);
        const isNum = !isNaN(numScore) && text !== '';
        
        let color = 'default';
        if (isNum) {
          if (numScore >= 90) color = 'success';
          else if (numScore >= 60) color = 'processing';
          else color = 'error';
        }
        
        return isSimulating ? (
          <Input
            defaultValue={text}
            onChange={(e) => handleInputChange(record.key, 'score', e.target.value)}
            onBlur={(e) => handleInputBlur(record.key, 'score', e.target.value)}
            onPressEnter={(e) => handleInputBlur(record.key, 'score', e.target.value)}
            size="small"
            style={{ width: 75 }}
            bordered={false}
            placeholder="成绩"
          />
        ) : (
          <Tag color={isNum ? color : 'blue'} style={{ minWidth: 50 }}>
            {text || '-'}
          </Tag>
        );
      },
    },
    {
      title: '绩点',
      dataIndex: 'gpa',
      key: 'gpa',
      width: 80,
      align: 'center',
      sorter: (a, b) => a.gpa - b.gpa,
      render: (text, record) => {
        if (editingKey === record.key && record.isCustom) {
          return (
            <InputNumber
              value={editForm.gpa}
              onChange={(v) => setEditForm({ ...editForm, gpa: v })}
              size="small"
              min={0}
              max={5}
              step={0.1}
              style={{ width: 65 }}
            />
          );
        }
        const gpa = parseFloat(text) || 0;
        return isSimulating ? (
          <InputNumber
            defaultValue={gpa || null}
            onChange={(v) => handleInputChange(record.key, 'gpa', v)}
            onBlur={(e) => handleInputBlur(record.key, 'gpa', editingValuesRef.current[record.key + 'gpa'] ?? gpa)}
            onPressEnter={(e) => handleInputBlur(record.key, 'gpa', editingValuesRef.current[record.key + 'gpa'] ?? gpa)}
            size="small"
            min={0}
            max={5}
            step={0.1}
            style={{ width: 65 }}
            bordered={false}
            placeholder="绩点"
          />
        ) : (
          <span className={gpa >= 3 ? 'gpa-high' : gpa >= 2 ? 'gpa-normal' : 'gpa-low'}>
            {gpa ? gpa.toFixed(2) : '-'}
          </span>
        );
      },
    },
    {
      title: '学期',
      dataIndex: 'term',
      key: 'term',
      width: 150,
      sorter: (a, b) => (a.term || '').localeCompare(b.term || ''),
      render: (text) => <span className="term-text">{text || '-'}</span>,
    },
    {
      title: '类型',
      dataIndex: 'courseType',
      key: 'courseType',
      width: 90,
      filters: [
        { text: '真实', value: 'real' },
        { text: '计划', value: 'plan' },
        { text: '模拟', value: 'simulated' },
      ],
      onFilter: (value, record) => {
        if (value === 'real') return record.isReal;
        if (value === 'plan') return record.fromPlan;
        if (value === 'simulated') return !record.isReal && !record.fromPlan;
        return false;
      },
      render: (text, record) => {
        if (record.isReal) return <Tag size="small" color="success">真实</Tag>;
        if (record.fromPlan) return <Tag size="small" color="processing">计划</Tag>;
        if (record.isCustom) return <Tag size="small" color="warning">自定义</Tag>;
        // isReal=false 且不是计划/自定义的 = 模拟（被修改过的真实课程）
        return <Tag size="small" color="default">模拟</Tag>;
      },
    },
    ...(isSimulating ? [{
      title: '操作',
      key: 'action',
      width: 80,
      align: 'center',
      render: (_, record) => (
        <Space size="small">
          {editingKey === record.key ? (
            <>
              <Button type="text" size="small" icon={<CheckCircleOutlined />} onClick={() => saveEdit(record.key)} />
              <Button type="text" size="small" icon={<CloseOutlined />} onClick={cancelEdit} />
            </>
          ) : (
            <>
              {record.isCustom && (
                <Button type="text" size="small" icon={<EditOutlined />} onClick={() => startEdit(record)} />
              )}
              <Popconfirm title="确认删除？" onConfirm={() => deleteCourse(record.key)} okText="删除" cancelText="取消">
                <Button type="text" size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    }] : []),
  ];

  // 培养计划表格列（带表头筛选）
  const planColumns = [
    { 
      title: '课程名称', 
      dataIndex: 'name', 
      key: 'name',
      width: 250,
      sorter: (a, b) => a.name.localeCompare(b.name),
      filterDropdown: ({ setSelectedKeys, selectedKeys, confirm, clearFilters }) => (
        <div style={{ padding: 8 }}>
          <Input
            placeholder="搜索课程名称"
            value={selectedKeys[0]}
            onChange={e => setSelectedKeys(e.target.value ? [e.target.value] : [])}
            onPressEnter={confirm}
            style={{ width: 200, marginBottom: 8, display: 'block' }}
          />
          <Space>
            <Button type="primary" onClick={confirm} size="small">搜索</Button>
            <Button onClick={clearFilters} size="small">重置</Button>
          </Space>
        </div>
      ),
      filterIcon: filtered => <SearchOutlined style={{ color: filtered ? '#1890ff' : undefined }} />,
      onFilter: (value, record) => record.name.toLowerCase().includes(value.toLowerCase()),
    },
    { 
      title: '学分', 
      dataIndex: 'credit', 
      key: 'credit', 
      width: 70, 
      align: 'center',
      sorter: (a, b) => a.credit - b.credit,
    },
    { 
      title: '类别', 
      dataIndex: 'category', 
      key: 'category', 
      width: 140,
      sorter: (a, b) => (a.category || '').localeCompare(b.category || ''),
      filters: [...new Set(getPlannedCourses.map(c => c.category).filter(Boolean))].map(c => ({ text: c, value: c })),
      onFilter: (value, record) => record.category === value,
    },
    { 
      title: '性质', 
      dataIndex: 'courseType', 
      key: 'courseType', 
      width: 80,
      filters: [...new Set(getPlannedCourses.map(c => c.courseType).filter(Boolean))].map(n => ({ text: n, value: n })),
      onFilter: (value, record) => record.courseType === value,
    },
    { 
      title: '学期', 
      dataIndex: 'term', 
      key: 'term', 
      width: 160,
      sorter: (a, b) => (a.term || '').localeCompare(b.term || ''),
      filters: [...new Set(getPlannedCourses.map(c => c.term).filter(Boolean))].map(t => ({ text: t, value: t })),
      onFilter: (value, record) => record.term === value,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      filters: [...new Set(getPlannedCourses.map(c => c.status).filter(Boolean))].map(s => ({ text: s, value: s })),
      onFilter: (value, record) => record.status === value,
      render: (text) => <Tag size="small">{text || '未修'}</Tag>,
    },
  ];

  // 筛选课程
  const filteredCourses = useMemo(() => {
    switch (activeTab) {
      case 'real': return courses.filter(c => c.isReal);
      case 'custom': return courses.filter(c => !c.isReal);
      case 'passed': return courses.filter(c => c.gpa > 0);
      case 'pending': return courses.filter(c => !c.gpa && !c.score);
      default: return courses;
    }
  }, [courses, activeTab]);

  if (!isSimulating) return null;

  return (
    <div className="gpa-calculator-embedded">
      {/* 统计卡片 */}
      <Row gutter={[16, 16]} className="gpa-stats-row">
        <Col xs={12} sm={8} md={4}>
          <Card size="small" className="stat-card">
            <Statistic title="课程总数" value={stats.totalCourses} prefix={<BookOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={6}>
          <Card size="small" className="stat-card highlight">
            <Statistic title="加权平均绩点" value={stats.weightedGPA} precision={4} prefix={<TrophyOutlined />} valueStyle={{ color: '#1890ff', fontWeight: 'bold' }} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={5}>
          <Card size="small" className="stat-card">
            <Statistic title="总学分" value={stats.totalCredits} precision={1} suffix="学分" />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={5}>
          <Card size="small" className="stat-card">
            <Statistic title="有效课程" value={stats.passedCount} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col xs={12} sm={8} md={4}>
          <Card size="small" className="stat-card">
            <Statistic title="模拟课程" value={stats.customCount} valueStyle={{ color: '#faad14' }} />
          </Card>
        </Col>
      </Row>

      {/* 工具栏 */}
      <div className="gpa-toolbar">
        <Space wrap>
          <Button type="primary" icon={<PlusOutlined />} onClick={addCustomCourse}>添加课程</Button>
          <Button icon={<ImportOutlined />} onClick={handleOpenImportDrawer}>从计划导入</Button>
          <Button icon={<CloudDownloadOutlined />} onClick={handleOpenFileModal}>从文件导入</Button>
          <Button icon={<CloudUploadOutlined />} onClick={handleOpenSaveModal} disabled={!courses.length}>保存</Button>
          <Button icon={<ReloadOutlined />} onClick={undo} disabled={historyIndex <= 0}>撤销</Button>
          {hasUnsavedChanges && <Badge status="processing" text="未保存" />}
        </Space>
        
        <Space>
          <Button onClick={() => setIsSimulating(false)}>退出模拟</Button>
        </Space>
      </div>

      {/* 标签页 */}
      <Tabs activeKey={activeTab} onChange={setActiveTab} size="small" className="gpa-tabs">
        <TabPane tab={`全部 ${courses.length}`} key="all" />
        <TabPane tab={`真实 ${stats.realCount}`} key="real" />
        <TabPane tab={`模拟 ${stats.customCount}`} key="custom" />
        <TabPane tab={`有绩点 ${stats.passedCount}`} key="passed" />
        <TabPane tab={`待输入 ${courses.filter(c => !c.gpa && !c.score).length}`} key="pending" />
      </Tabs>

      {/* 课程列表 */}
      <Table
        columns={columns}
        dataSource={filteredCourses}
        rowKey="key"
        size="small"
        pagination={{ pageSize: 10, showSizeChanger: true, pageSizeOptions: ['10', '20', '50'], showTotal: (total) => `共 ${total} 门课程` }}
        scroll={{ x: 'max-content' }}
        className="gpa-table"
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无课程，请添加或导入" /> }}
      />

      {/* 提示信息 */}
      <Alert
        message="GPA模拟说明"
        description={<ul className="gpa-tips"><li>成绩和绩点直接使用教务系统返回的原始数据</li><li>总GPA = Σ(绩点×学分)/Σ学分（加权平均）</li><li>可直接点击成绩、绩点、学分进行编辑</li><li>数据导出为JSON文件保存在本地</li></ul>}
        type="info"
        showIcon
        style={{ marginTop: 16 }}
      />

      {/* 培养计划导入抽屉 - 新版树形导航设计 */}
      <Drawer
        title={
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span>从培养计划导入课程</span>
            <span style={{ fontSize: '13px', fontWeight: 'normal', color: '#666' }}>
              已选择 {selectedPlanCourses.length} 门课程
            </span>
          </div>
        }
        placement="right"
        width="75%"
        open={importDrawerVisible}
        onClose={() => { 
          setImportDrawerVisible(false); 
          setPlanSearchText('');
          setSelectedPlanCourses([]);
        }}
        footer={
          <Space style={{ float: 'right' }}>
            <Button onClick={() => { 
              setImportDrawerVisible(false); 
              setPlanSearchText(''); 
              setSelectedPlanCourses([]);
            }}>
              取消
            </Button>
            <Button type="primary" onClick={importFromPlan} disabled={selectedPlanCourses.length === 0}>
              导入
            </Button>
          </Space>
        }
        className="plan-import-drawer"
      >
        {planLoading ? (
          <div style={{ textAlign: 'center', padding: '60px' }}>
            <Spin tip="正在加载培养计划..." />
          </div>
        ) : (
          <div className="plan-import-container">
            {/* 左侧：分类树 */}
            <div className="plan-import-sidebar">
              <div className="sidebar-header">
                <Text strong style={{ fontSize: '14px' }}>
                  <FolderOutlined style={{ marginRight: 6 }} />课程分类
                </Text>
              </div>
              
              {/* 筛选选项 */}
              <div className="sidebar-filters">
                <Checkbox 
                  checked={showOnlyDeficit}
                  onChange={(e) => setShowOnlyDeficit(e.target.checked)}
                >
                  只看差学分
                </Checkbox>
              </div>
              
              <Divider style={{ margin: '12px 0' }} />
              
              {/* 树形导航 */}
              <div className="tree-container">
                <Tree
                  treeData={[
                    {
                      title: (
                        <div className="import-tree-node">
                          <span className="node-name" style={{ color: '#1890ff' }}>全部课程</span>
                          <span className="course-count">{academicPlan.length}门</span>
                        </div>
                      ),
                      key: 'all',
                      selectable: true,
                      icon: <FolderOutlined style={{ color: '#1890ff' }} />,
                    },
                    ...buildImportTreeData(planCategories, expandedKeys)
                  ]}
                  selectedKeys={[selectedCategoryKey]}
                  expandedKeys={expandedKeys}
                  onExpand={(keys) => setExpandedKeys(keys)}
                  onSelect={(keys, info) => {
                    if (keys.length > 0) {
                      setSelectedCategoryKey(keys[0]);
                    } else {
                      setSelectedCategoryKey('all');
                    }
                  }}
                  showIcon
                  blockNode
                />
              </div>
            </div>
            
            {/* 右侧：课程列表 */}
            <div className="plan-import-content">
              {/* 工具栏 - 快速筛选按钮 */}
              <div className="content-toolbar">
                <Input.Search
                  placeholder="搜索课程名称或代码"
                  value={planSearchText}
                  onChange={(e) => setPlanSearchText(e.target.value)}
                  allowClear
                  style={{ width: 200 }}
                  size="small"
                />
                <Space size="small" className="quick-filters">
                  {/* 性质筛选组 - 互斥 */}
                  <Button.Group>
                    <Button
                      size="small"
                      type={quickFilters.includes('必修') ? 'primary' : 'default'}
                      onClick={() => {
                        setQuickFilters(prev => {
                          const withoutNature = prev.filter(f => f !== '必修' && f !== '选修');
                          if (prev.includes('必修')) {
                            return withoutNature;
                          } else {
                            return [...withoutNature, '必修'];
                          }
                        });
                      }}
                    >
                      必修
                    </Button>
                    <Button
                      size="small"
                      type={quickFilters.includes('选修') ? 'primary' : 'default'}
                      onClick={() => {
                        setQuickFilters(prev => {
                          const withoutNature = prev.filter(f => f !== '必修' && f !== '选修');
                          if (prev.includes('选修')) {
                            return withoutNature;
                          } else {
                            return [...withoutNature, '选修'];
                          }
                        });
                      }}
                    >
                      选修
                    </Button>
                  </Button.Group>
                  
                  <div className="filter-divider" />
                  
                  {/* 状态筛选组 - 互斥 */}
                  <Button.Group>
                    <Button
                      size="small"
                      type={quickFilters.includes('已选课') ? 'primary' : 'default'}
                      onClick={() => {
                        setQuickFilters(prev => {
                          const withoutStatus = prev.filter(f => f !== '已选课' && f !== '未修读');
                          if (prev.includes('已选课')) {
                            return withoutStatus;
                          } else {
                            return [...withoutStatus, '已选课'];
                          }
                        });
                      }}
                    >
                      已选课
                    </Button>
                    <Button
                      size="small"
                      type={quickFilters.includes('未修读') ? 'primary' : 'default'}
                      onClick={() => {
                        setQuickFilters(prev => {
                          const withoutStatus = prev.filter(f => f !== '已选课' && f !== '未修读');
                          if (prev.includes('未修读')) {
                            return withoutStatus;
                          } else {
                            return [...withoutStatus, '未修读'];
                          }
                        });
                      }}
                    >
                      未修读
                    </Button>
                  </Button.Group>
                </Space>
              </div>
              
              {/* 课程表格 */}
              <Table
                rowSelection={{ 
                  selectedRowKeys: selectedPlanCourses, 
                  onChange: setSelectedPlanCourses 
                }}
                columns={[
                  { 
                    title: '课程名称', 
                    dataIndex: 'name', 
                    key: 'name',
                    width: 240,
                    sorter: (a, b) => (a.name || '').localeCompare(b.name || ''),
                    render: (text) => <span className="course-name-cell">{text}</span>
                  },
                  { 
                    title: '学分', 
                    dataIndex: 'credit', 
                    key: 'credit', 
                    width: 70, 
                    align: 'center',
                    sorter: (a, b) => a.credit - b.credit,
                  },
                  { 
                    title: '性质', 
                    dataIndex: 'courseType', 
                    key: 'courseType', 
                    width: 80,
                    align: 'center',
                    sorter: (a, b) => (a.courseType || '').localeCompare(b.courseType || ''),
                    filters: [
                      { text: '必修', value: '必修' },
                      { text: '选修', value: '选修' },
                    ],
                    onFilter: (value, record) => record.courseType === value,
                    render: (text) => (
                      <Tag size="small" color={text === '必修' ? 'blue' : 'green'}>
                        {text || '-'}
                      </Tag>
                    )
                  },
                  { 
                    title: '学期', 
                    dataIndex: 'term', 
                    key: 'term', 
                    width: 180,
                    sorter: (a, b) => (a.term || '').localeCompare(b.term || ''),
                    filters: [...new Set(getPlannedCourses.map(c => c.term).filter(Boolean))].map(t => ({ text: t, value: t })),
                    onFilter: (value, record) => record.term === value,
                    render: (text) => <span className="term-cell">{text}</span>
                  },
                  { 
                    title: '状态', 
                    dataIndex: 'status', 
                    key: 'status', 
                    width: 90,
                    align: 'center',
                    sorter: (a, b) => (a.status || '').localeCompare(b.status || ''),
                    filters: [
                      { text: '已选课', value: '已选课' },
                      { text: '未修读', value: '未修' },
                    ],
                    onFilter: (value, record) => {
                      if (value === '已选课') return record.status === '已选课' || record.status === '已选';
                      if (value === '未修') return record.status === '未修' || record.status === '待选' || !record.status;
                      return record.status === value;
                    },
                    render: (text) => {
                      const statusMap = {
                        '已选课': { color: 'blue', text: '已选课' },
                        '已选': { color: 'blue', text: '已选课' },
                        '未修': { color: 'default', text: '未修读' },
                        '未修读': { color: 'default', text: '未修读' },
                        '待选': { color: 'default', text: '未修读' },
                      };
                      const status = statusMap[text] || { color: 'default', text: text || '未修读' };
                      return <Tag size="small" color={status.color}>{status.text}</Tag>;
                    }
                  },
                ]}
                dataSource={getPlannedCourses}
                rowKey="key"
                size="small"
                pagination={{ 
                  defaultPageSize: 20,
                  pageSizeOptions: ['10', '20', '50', '100'],
                  showSizeChanger: true,
                  showTotal: (total) => `共 ${total} 门`,
                }}
                locale={{ emptyText: <Empty description="没有可导入的课程" /> }}
              />
            </div>
          </div>
        )}
      </Drawer>

      {/* 服务器文件导入弹窗 */}
      <Modal
        title="从服务器导入GPA模拟文件"
        open={importFileModalVisible}
        onCancel={() => setImportFileModalVisible(false)}
        footer={null}
        width={700}
      >
        {filesLoading ? (
          <div style={{ textAlign: 'center', padding: '40px' }}><Spin tip="加载文件列表..." /></div>
        ) : serverFiles.length > 0 ? (
          <List
            dataSource={serverFiles}
            renderItem={item => (
              <List.Item
                actions={[
                  <Button type="primary" size="small" icon={<CloudDownloadOutlined />} onClick={() => handleImportFromServer(item.filename)}>导入</Button>,
                  <Button size="small" icon={<EditOutlined />} onClick={() => handleOpenRenameModal(item.filename)}>重命名</Button>,
                  <Popconfirm title="确认删除？" onConfirm={() => handleDeleteServerFile(item.filename)}>
                    <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={<Space><FileTextOutlined />{item.filename}</Space>}
                  description={
                    <Space direction="vertical" size={0}>
                      <Typography.Text type="secondary">修改时间: {new Date(item.modified_time).toLocaleString()}</Typography.Text>
                      {item.stats && (
                        <Typography.Text type="secondary">课程: {item.stats.totalCourses}门 | GPA: {item.stats.weightedGPA?.toFixed(3)}</Typography.Text>
                      )}
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        ) : (
          <Empty description="服务器上没有GPA模拟文件" />
        )}
      </Modal>

      {/* 保存文件弹窗 */}
      <Modal
        title="保存GPA模拟"
        open={saveModalVisible}
        onOk={handleConfirmSave}
        onCancel={() => setSaveModalVisible(false)}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ marginBottom: 16 }}>
          <Typography.Text type="secondary">文件名（无需添加.json后缀）</Typography.Text>
        </div>
        <Input
          placeholder="输入文件名"
          value={saveFilename}
          onChange={(e) => setSaveFilename(e.target.value)}
          onPressEnter={handleConfirmSave}
          autoFocus
        />
      </Modal>

      {/* 重命名文件弹窗 */}
      <Modal
        title="重命名文件"
        open={renameModalVisible}
        onOk={handleConfirmRename}
        onCancel={() => setRenameModalVisible(false)}
        okText="确认"
        cancelText="取消"
      >
        <div style={{ marginBottom: 16 }}>
          <Typography.Text type="secondary">原文件: {renameOldFile}</Typography.Text>
        </div>
        <Input
          placeholder="输入新文件名"
          value={renameNewName}
          onChange={(e) => setRenameNewName(e.target.value)}
          onPressEnter={handleConfirmRename}
          autoFocus
        />
      </Modal>

      {/* 导入冲突选择弹窗 */}
      <Modal
        title={<Space><WarningOutlined style={{ color: '#faad14' }} /><span>检测到同名课程数据不同</span></Space>}
        open={importConflictModalVisible}
        onOk={confirmImportConflicts}
        onCancel={() => setImportConflictModalVisible(false)}
        width={800}
        footer={[
          <Button key="cancel" onClick={() => setImportConflictModalVisible(false)}>取消导入</Button>,
          <Button key="all-imported" onClick={() => setAllConflictChoices('imported')}>全部使用导入的</Button>,
          <Button key="all-existing" onClick={() => setAllConflictChoices('existing')}>全部保留现有的</Button>,
          <Button key="confirm" type="primary" onClick={confirmImportConflicts}>确认导入</Button>,
        ]}
      >
        <Alert 
          message="以下课程与现有课程名称相同但数据不同" 
          description="请为每门课程选择保留导入的数据还是现有的数据" 
          type="warning" 
          showIcon 
          style={{ marginBottom: 16 }} 
        />
        <Table
          dataSource={importConflicts}
          rowKey="key"
          size="small"
          pagination={false}
          scroll={{ y: 400 }}
          columns={[
            { title: '课程名称', dataIndex: 'name', key: 'name', width: 150 },
            { 
              title: '导入的数据', 
              key: 'imported', 
              width: 180,
              render: (_, r) => (
                <div style={{ 
                  padding: 8, 
                  borderRadius: 4, 
                  background: r.choice === 'imported' ? '#e6f7ff' : '#f5f5f5',
                  border: r.choice === 'imported' ? '1px solid #1890ff' : '1px solid #d9d9d9',
                }}>
                  <div>成绩: {r.imported.score || '-'}</div>
                  <div>绩点: {r.imported.gpa}</div>
                  <div>学分: {r.imported.credit}</div>
                  {r.choice === 'imported' && <Tag color="blue" size="small">将使用</Tag>}
                </div>
              )
            },
            { 
              title: '现有的数据', 
              key: 'existing', 
              width: 180,
              render: (_, r) => (
                <div style={{ 
                  padding: 8, 
                  borderRadius: 4, 
                  background: r.choice === 'existing' ? '#f6ffed' : '#f5f5f5',
                  border: r.choice === 'existing' ? '1px solid #52c41a' : '1px solid #d9d9d9',
                }}>
                  <div>成绩: {r.existing.score || '-'}</div>
                  <div>绩点: {r.existing.gpa}</div>
                  <div>学分: {r.existing.credit}</div>
                  {r.choice === 'existing' && <Tag color="green" size="small">保留</Tag>}
                </div>
              )
            },
            {
              title: '选择',
              key: 'choice',
              width: 120,
              align: 'center',
              render: (_, r) => (
                <Space direction="vertical" size="small">
                  <Button 
                    size="small" 
                    type={r.choice === 'imported' ? 'primary' : 'default'}
                    onClick={() => handleConflictChoiceChange(r.key, 'imported')}
                  >
                    使用导入的
                  </Button>
                  <Button 
                    size="small" 
                    type={r.choice === 'existing' ? 'primary' : 'default'}
                    onClick={() => handleConflictChoiceChange(r.key, 'existing')}
                  >
                    保留现有的
                  </Button>
                </Space>
              )
            },
          ]}
        />
      </Modal>

      {/* 冲突检测弹窗 */}
      <Modal
        title={<Space><WarningOutlined style={{ color: '#faad14' }} /><span>检测到成绩更新</span></Space>}
        open={conflictModalVisible}
        onCancel={() => setConflictModalVisible(false)}
        footer={[
          <Button key="keep" onClick={() => resolveConflicts(false)}>保留模拟成绩</Button>,
          <Button key="update" type="primary" onClick={() => resolveConflicts(true)}>更新为真实成绩</Button>,
        ]}
      >
        <Alert message="以下课程的真实成绩与模拟成绩不同" description="您可以选择保留模拟成绩，或更新为最新的真实成绩" type="warning" showIcon style={{ marginBottom: 16 }} />
        <Table
          dataSource={conflicts}
          rowKey="code"
          size="small"
          pagination={false}
          columns={[
            { title: '课程', dataIndex: 'name', key: 'name' },
            { title: '模拟成绩', key: 'imported', render: (_, r) => `${r.imported.score} / ${r.imported.gpa}绩点` },
            { title: '真实成绩', key: 'real', render: (_, r) => <span style={{ color: '#52c41a', fontWeight: 'bold' }}>{r.real.score} / {r.real.gpa}绩点</span> },
          ]}
        />
      </Modal>
    </div>
  );
});

export default GPACalculator;
