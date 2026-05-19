import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Tag, Space, Modal, Descriptions, Spin,
  message, Alert, Tooltip, Progress, Select, Typography, Divider,
  Switch, Popconfirm, Badge, Radio, Input, List, Form,
} from 'antd';
import {
  StarOutlined, EyeOutlined, ThunderboltOutlined,
  SafetyCertificateOutlined, ExclamationCircleOutlined,
  CheckCircleOutlined, CloseCircleOutlined, TrophyOutlined,
  LoadingOutlined, LeftOutlined, UnorderedListOutlined,
  EditOutlined, FileTextOutlined,
} from '@ant-design/icons';
import {
  getEvaluationTasks, getEvaluationCourses, getEvaluationIndicators,
  submitEvaluation, batchEvaluation,
} from '../services/api';
import './EvaluationPage.css';

const { Text, Title } = Typography;
const { TextArea } = Input;

// dfdj 评分等级映射
const SCORE_MAP = { 6: 100, 5: 90, 4: 80, 3: 70, 2: 60, 1: 50 };
const SCORE_LABELS = {
  6: '优秀 (100)', 5: '很好 (90)', 4: '好 (80)',
  3: '较好 (70)', 2: '一般 (60)', 1: '较差 (50)',
};
const SCORE_OPTIONS = [
  { value: 6, label: '优秀 (100)' },
  { value: 5, label: '很好 (90)' },
  { value: 4, label: '好 (80)' },
  { value: 3, label: '较好 (70)' },
  { value: 2, label: '一般 (60)' },
  { value: 1, label: '较差 (50)' },
];

const EvaluationPage = () => {
  // ── 状态 ────────────────────────────────────────────────────────────────
  const [tasks, setTasks] = useState([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [selectedTask, setSelectedTask] = useState(null);
  const [courses, setCourses] = useState([]);
  const [coursesLoading, setCoursesLoading] = useState(false);

  // 详情弹窗（已评课程查看详情）
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [currentDetail, setCurrentDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 全局策略
  const [globalStrategy, setGlobalStrategy] = useState('highest');

  // 批量评教
  const [selectedXspjIds, setSelectedXspjIds] = useState([]);
  const [batchRunning, setBatchRunning] = useState(false);

  // 安全开关
  const [safetyMode, setSafetyMode] = useState(true);

  // 单独评价弹窗
  const [evaluateModalVisible, setEvaluateModalVisible] = useState(false);
  const [evaluateCourse, setEvaluateCourse] = useState(null);
  const [evaluateIndicators, setEvaluateIndicators] = useState([]);
  const [evaluateLoading, setEvaluateLoading] = useState(false);
  const [evaluateSubmitting, setEvaluateSubmitting] = useState(false);

  // 批量进度弹窗
  const [progressModalVisible, setProgressModalVisible] = useState(false);
  const [progressCurrent, setProgressCurrent] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [progressResults, setProgressResults] = useState([]);
  const [progressCourseName, setProgressCourseName] = useState('');

  // ── 加载任务列表（一级） ────────────────────────────────────────────────
  const loadTasks = useCallback(async () => {
    setTasksLoading(true);
    try {
      const data = await getEvaluationTasks();
      setTasks(data.tasks || []);
    } catch (error) {
      message.error('获取评教任务失败');
      console.error(error);
    } finally {
      setTasksLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // ── 选中任务 → 加载课程列表（二级） ────────────────────────────────────
  const handleSelectTask = async (task) => {
    setSelectedTask(task);
    setCoursesLoading(true);
    setSelectedXspjIds([]);
    try {
      const data = await getEvaluationCourses(task.task_id);
      setCourses(data.courses || []);
    } catch (error) {
      message.error('获取课程列表失败');
      console.error(error);
    } finally {
      setCoursesLoading(false);
    }
  };

  // ── 查看详情（已评课程） ────────────────────────────────────────────────
  const handleViewDetail = async (record) => {
    setDetailLoading(true);
    setDetailModalVisible(true);
    try {
      const data = await getEvaluationIndicators(record.xspjid, record.task_id);
      setCurrentDetail({
        ...data,
        course_name: record.course_name,
        teacher_name: record.teacher_name,
        is_evaluated: record.is_evaluated,
        score: record.score,
        avg_score: record.avg_score,
      });
    } catch (error) {
      message.error('获取评教指标失败');
    } finally {
      setDetailLoading(false);
    }
  };

  // ── 打开单独评价弹窗 ───────────────────────────────────────────────────
  const handleOpenEvaluate = async (record) => {
    setEvaluateCourse(record);
    setEvaluateModalVisible(true);
    setEvaluateLoading(true);
    setEvaluateIndicators([]);
    try {
      const data = await getEvaluationIndicators(record.xspjid, record.task_id);
      const indicators = (data.indicators || []).map(ind => ({
        ...ind,
        // 默认未选择
        _customDfdj: ind.evaltype === 1 ? undefined : ind.dfdj,
        _customResult: ind.result || '',
      }));
      setEvaluateIndicators(indicators);
    } catch (error) {
      message.error('获取评教指标失败');
    } finally {
      setEvaluateLoading(false);
    }
  };

  // ── 修改单独评价的某题分数 ──────────────────────────────────────────────
  const handleScoreChange = (zbid, value) => {
    setEvaluateIndicators(prev =>
      prev.map(ind =>
        ind.zbid === zbid ? { ...ind, _customDfdj: value } : ind
      )
    );
  };

  // ── 修改单独评价的文本结果 ──────────────────────────────────────────────
  const handleResultChange = (zbid, value) => {
    setEvaluateIndicators(prev =>
      prev.map(ind =>
        ind.zbid === zbid ? { ...ind, _customResult: value } : ind
      )
    );
  };

  // ── 应用策略填充到评价弹窗 ──────────────────────────────────────────────
  const handleApplyStrategy = (strategy) => {
    setEvaluateIndicators(prev => {
      let selectionCount = 0;
      return prev.map(ind => {
        if (ind.evaltype !== 1) return ind;
        let dfdj;
        if (strategy === 'highest') {
          if (ind.sfdx === 1) {
            dfdj = [6, 5];
          } else {
            dfdj = selectionCount === 0 ? 5 : 6;
          }
        } else {
          if (ind.sfdx === 1) {
            dfdj = selectionCount === 0 ? [1, 2] : [1];
          } else {
            dfdj = selectionCount === 0 ? 2 : 1;
          }
        }
        selectionCount += 1;
        return { ...ind, _customDfdj: dfdj };
      });
    });
  };

  // ── 提交单独评价 ────────────────────────────────────────────────────────
  const handleSubmitEvaluate = async () => {
    if (!evaluateCourse || !selectedTask) return;

    // 验证
    const errors = [];
    const selectionScores = [];
    for (const ind of evaluateIndicators) {
      if (ind.evaltype === 1) {
        if (ind.sfbt === 1 && (ind._customDfdj === undefined || ind._customDfdj === null)) {
          errors.push(`必填指标未评分: ${ind.zbmc}`);
        }
        if (ind._customDfdj !== undefined && ind._customDfdj !== null) {
          if (Array.isArray(ind._customDfdj)) {
            selectionScores.push(...ind._customDfdj);
          } else {
            selectionScores.push(ind._customDfdj);
          }
        }
      } else if (ind.sfbt === 1 && !ind._customResult) {
        errors.push(`必填文本指标未填写: ${ind.zbmc}`);
      }
    }
    if (selectionScores.length > 1 && new Set(selectionScores.map(String)).size === 1) {
      errors.push('评价选项不能全部相同');
    }
    if (errors.length > 0) {
      Modal.error({ title: '评分验证失败', content: errors.join('；') });
      return;
    }

    if (safetyMode) {
      // 安全模式：仅预览
      const previewItems = evaluateIndicators.map(ind => {
        const item = { zbid: ind.zbid, zbmc: ind.zbmc, evaltype: ind.evaltype };
        if (ind.evaltype === 1) {
          const dfdj = ind._customDfdj;
          if (Array.isArray(dfdj)) {
            item.dfdj = dfdj;
            item.score = dfdj.map(v => SCORE_MAP[v] || 0);
          } else if (dfdj !== undefined && dfdj !== null) {
            item.dfdj = dfdj;
            item.score = SCORE_MAP[dfdj] || 0;
          } else {
            item.dfdj = null;
            item.score = 0;
          }
        } else {
          item.result = ind._customResult || '';
        }
        return item;
      });

      Modal.info({
        title: '安全模式 - 评分预览',
        width: 600,
        content: (
          <div style={{ marginTop: 16 }}>
            <Table
              dataSource={previewItems}
              columns={previewIndicatorColumns}
              rowKey="zbid"
              size="small"
              pagination={false}
              scroll={{ y: 300 }}
            />
          </div>
        ),
        onOk: () => {},
      });
      return;
    }

    // 实际提交
    setEvaluateSubmitting(true);
    try {
      // 构建 custom_scores 和 text_results
      const customScores = {};
      const textResults = {};
      for (const ind of evaluateIndicators) {
        if (ind.evaltype === 1 && ind._customDfdj !== undefined && ind._customDfdj !== null) {
          customScores[ind.zbid] = ind._customDfdj;
        } else if (ind.evaltype !== 1 && ind._customResult) {
          textResults[ind.zbid] = ind._customResult;
        }
      }

      const result = await submitEvaluation(
        selectedTask.task_id,
        evaluateCourse.xspjid,
        'custom',
        customScores,
        false,
        textResults
      );
      if (result.success) {
        message.success(`${evaluateCourse.course_name} 评教提交成功`);
        setEvaluateModalVisible(false);
        if (selectedTask) handleSelectTask(selectedTask);
      } else {
        message.error(`提交失败: ${result.message}`);
      }
    } catch (error) {
      message.error('提交异常');
    } finally {
      setEvaluateSubmitting(false);
    }
  };

  // ── 批量评教 ────────────────────────────────────────────────────────────
  const handleBatchEvaluate = () => {
    if (!selectedTask) {
      message.warning('请先选择评教任务');
      return;
    }
    const pendingCourses = courses.filter(c => !c.is_evaluated);

    if (selectedXspjIds.length === 0) {
      message.warning('请先勾选至少一门待评课程');
      return;
    }
    const targetCourses = pendingCourses.filter(c => selectedXspjIds.includes(c.xspjid));

    if (safetyMode) {
      Modal.info({
        title: '安全模式 - 批量预览',
        content: `将预览 ${targetCourses.length} 门课程的评分方案，不会实际提交。`,
        onOk: async () => {
          setBatchRunning(true);
          try {
            const data = await batchEvaluation(selectedTask.task_id, globalStrategy, null, true, selectedXspjIds);
            const successCount = data.success_count || 0;
            message.info(`预览完成：${successCount}/${data.pending_count} 门课程验证通过`);
          } catch (error) {
            message.error('批量预览失败');
          } finally {
            setBatchRunning(false);
          }
        },
      });
      return;
    }

    // 非安全模式：实际批量提交（带进度条）
    Modal.confirm({
      title: '确认批量提交评教',
      content: `将对 ${targetCourses.length} 门课程提交评教。评教系统不支持重试！`,
      okText: '确认提交',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchRunning(true);
        setProgressModalVisible(true);
        setProgressTotal(targetCourses.length);
        setProgressCurrent(0);
        setProgressResults([]);

        const results = [];
        for (let i = 0; i < targetCourses.length; i++) {
          const course = targetCourses[i];
          setProgressCurrent(i + 1);
          setProgressCourseName(course.course_name);

          try {
            const result = await submitEvaluation(
              selectedTask.task_id,
              course.xspjid,
              globalStrategy,
              null,
              false
            );
            const item = {
              course_name: course.course_name,
              teacher_name: course.teacher_name,
              success: result.success,
              message: result.message || (result.success ? '提交成功' : '提交失败'),
              avg_score: result.data?.task?.zpf,
            };
            results.push(item);
            setProgressResults(prev => [...prev, item]);

            if (result.success) {
              message.success(`${course.course_name} 提交成功`);
            } else {
              message.error(`${course.course_name} 提交失败: ${result.message}`);
            }
          } catch (error) {
            const item = {
              course_name: course.course_name,
              teacher_name: course.teacher_name,
              success: false,
              message: '提交异常',
            };
            results.push(item);
            setProgressResults(prev => [...prev, item]);
            message.error(`${course.course_name} 提交异常`);
          }

          // 间隔 2 秒（最后一个不需要）
          if (i < targetCourses.length - 1) {
            await new Promise(r => setTimeout(r, 2000));
          }
        }

        const successCount = results.filter(r => r.success).length;
        message.info(`批量评教完成：成功 ${successCount}/${targetCourses.length}`);

        // 刷新
        handleSelectTask(selectedTask);
        loadTasks();
        setBatchRunning(false);
      },
    });
  };

  // ── 关闭进度弹窗 ────────────────────────────────────────────────────────
  const handleCloseProgress = () => {
    setProgressModalVisible(false);
  };

  // ── 一级任务列 ──────────────────────────────────────────────────────────
  const taskColumns = [
    {
      title: '任务名称',
      dataIndex: 'task_name',
      key: 'task_name',
      ellipsis: true,
    },
    {
      title: '总课程数',
      dataIndex: 'total_count',
      key: 'total_count',
      width: 100,
      align: 'center',
    },
    {
      title: '待评',
      dataIndex: 'pending_count',
      key: 'pending_count',
      width: 80,
      align: 'center',
      render: (val) => val > 0
        ? <Badge count={val} style={{ backgroundColor: '#fa8c16' }} />
        : <Text type="success">0</Text>,
    },
    {
      title: '已评',
      dataIndex: 'evaluated_count',
      key: 'evaluated_count',
      width: 80,
      align: 'center',
      render: (val) => <Text type="success">{val}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, record) => (
        <Button
          size="small"
          type={selectedTask?.task_id === record.task_id ? 'default' : 'primary'}
          icon={<UnorderedListOutlined />}
          onClick={() => handleSelectTask(record)}
        >
          {selectedTask?.task_id === record.task_id ? '已选中' : '查看课程'}
        </Button>
      ),
    },
  ];

  // ── 待评课程列 ──────────────────────────────────────────────────────────
  const pendingColumns = [
    {
      title: '课程名称',
      dataIndex: 'course_name',
      key: 'course_name',
      width: 220,
      ellipsis: true,
    },
    {
      title: '教师姓名',
      dataIndex: 'teacher_name',
      key: 'teacher_name',
      width: 100,
    },
    {
      title: '开课单位',
      dataIndex: 'department',
      key: 'department',
      width: 150,
      ellipsis: true,
    },
    {
      title: '评价状态',
      dataIndex: 'is_evaluated',
      key: 'is_evaluated',
      width: 100,
      render: () => <Tag color="orange" icon={<ExclamationCircleOutlined />}>未评</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, record) => (
        <Button
          size="small"
          type="primary"
          icon={<EditOutlined />}
          onClick={() => handleOpenEvaluate(record)}
        >
          评价
        </Button>
      ),
    },
  ];

  // ── 已评课程列 ──────────────────────────────────────────────────────────
  const completedColumns = [
    {
      title: '课程名称',
      dataIndex: 'course_name',
      key: 'course_name',
      width: 220,
      ellipsis: true,
    },
    {
      title: '教师姓名',
      dataIndex: 'teacher_name',
      key: 'teacher_name',
      width: 100,
    },
    {
      title: '开课单位',
      dataIndex: 'department',
      key: 'department',
      width: 150,
      ellipsis: true,
    },
    {
      title: '课程属性',
      dataIndex: 'course_type_name',
      key: 'course_type_name',
      width: 100,
      ellipsis: true,
    },
    {
      title: '评价状态',
      dataIndex: 'is_evaluated',
      key: 'is_evaluated',
      width: 100,
      render: () => <Tag color="green" icon={<CheckCircleOutlined />}>已评</Tag>,
    },
    {
      title: '已评均分',
      dataIndex: 'avg_score',
      key: 'avg_score',
      width: 100,
      align: 'center',
      render: (val) => val !== undefined && val !== null
        ? <Text strong style={{ color: '#52c41a' }}>{val}</Text>
        : <Text type="secondary">-</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, record) => (
        <Button
          size="small"
          icon={<FileTextOutlined />}
          onClick={() => handleViewDetail(record)}
        >
          详情
        </Button>
      ),
    },
  ];

  // ── 详情弹窗中的指标列（已评课程，不显示选项） ──────────────────────────
  const detailIndicatorColumns = [
    {
      title: '指标名称',
      dataIndex: 'zbmc',
      key: 'zbmc',
      width: 200,
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'evaltype',
      key: 'evaltype',
      width: 80,
      render: (type) => type === 1 ? '选择' : '文本',
    },
    {
      title: '多选',
      dataIndex: 'sfdx',
      key: 'sfdx',
      width: 60,
      render: (val) => val === 1 ? '是' : '否',
    },
    {
      title: '必填',
      dataIndex: 'sfbt',
      key: 'sfbt',
      width: 60,
      render: (val) => val === 1 ? <Tag color="red">必填</Tag> : <Tag>选填</Tag>,
    },
    {
      title: '权重',
      dataIndex: 'weight',
      key: 'weight',
      width: 80,
      render: (val) => val > 0 ? `${val}%` : '-',
    },
    {
      title: '分值',
      dataIndex: 'fz',
      key: 'fz',
      width: 80,
      render: (val) => val > 0 ? `${val}` : '-',
    },
    {
      title: '已评等级',
      dataIndex: 'dfdj',
      key: 'dfdj',
      width: 120,
      render: (val) => {
        if (val === undefined || val === null) return <Text type="secondary">-</Text>;
        if (Array.isArray(val)) {
          return val.map(v => <Tag key={v} color="blue">{SCORE_LABELS[v] || v}</Tag>);
        }
        return <Tag color="blue">{SCORE_LABELS[val] || val}</Tag>;
      },
    },
    {
      title: '已评分数',
      dataIndex: 'score',
      key: 'score',
      width: 100,
      render: (val) => {
        if (val === undefined || val === null) return <Text type="secondary">-</Text>;
        if (Array.isArray(val)) {
          return val.map(v => <Text key={v} strong type="success">{v}</Text>);
        }
        return <Text strong type="success">{val}</Text>;
      },
    },
  ];

  // ── 预览指标列 ──────────────────────────────────────────────────────────
  const previewIndicatorColumns = [
    {
      title: '指标名称',
      dataIndex: 'zbmc',
      key: 'zbmc',
      width: 200,
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'evaltype',
      key: 'evaltype',
      width: 80,
      render: (type) => type === 1 ? '选择' : '文本',
    },
    {
      title: '评分等级',
      dataIndex: 'dfdj',
      key: 'dfdj',
      width: 120,
      render: (val) => {
        if (!val && val !== 0) return '-';
        if (Array.isArray(val)) return val.map(v => SCORE_LABELS[v] || v).join(', ');
        return SCORE_LABELS[val] || `${val}`;
      },
    },
    {
      title: '对应分数',
      dataIndex: 'score',
      key: 'score',
      width: 120,
      render: (val) => {
        if (Array.isArray(val)) return val.map(v => <Text key={v} strong type="success">{v}</Text>);
        return val !== undefined ? <Text strong type="success">{val}</Text> : '-';
      },
    },
  ];

  // ── 统计 ────────────────────────────────────────────────────────────────
  const pendingCourses = courses.filter(c => !c.is_evaluated);
  const completedCourses = courses.filter(c => c.is_evaluated);

  return (
    <div className="evaluation-page">
      {/* 顶部安全提示 */}
      <Alert
        message="评教安全模式已开启"
        description="当前为安全模式，所有提交操作仅预览不实际提交。如需实际提交，请关闭安全模式开关。评教系统不支持重试，请谨慎操作！"
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        action={
          <Space align="center">
            <Text>安全模式</Text>
            <Switch
              checked={safetyMode}
              onChange={setSafetyMode}
              checkedChildren="开"
              unCheckedChildren="关"
            />
          </Space>
        }
      />

      {/* 控制面板 */}
      <Card
        title={
          <Space>
            <StarOutlined />
            <span>教学质量评价</span>
          </Space>
        }
        extra={
          <Space>
            <Button onClick={loadTasks} loading={tasksLoading}>
              刷新任务
            </Button>
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        {/* 策略选择 */}
        <Space wrap style={{ marginBottom: 16 }}>
          <Text strong>评分策略：</Text>
          <Select
            value={globalStrategy}
            onChange={setGlobalStrategy}
            style={{ width: 200 }}
            options={[
              { value: 'highest', label: '最高分（首题5其余6）' },
              { value: 'lowest', label: '最低分（首题2其余1）' },
            ]}
          />
          {selectedTask && (
            <>
              <Divider type="vertical" />
              <Text type="secondary">
                待评: <Text strong style={{ color: '#fa8c16' }}>{pendingCourses.length}</Text>
                &nbsp;已评: <Text strong style={{ color: '#52c41a' }}>{completedCourses.length}</Text>
              </Text>
            </>
          )}
        </Space>

        {/* 批量操作 */}
        {pendingCourses.length > 0 && (
          <Space style={{ marginBottom: 16 }}>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={() => setSelectedXspjIds(pendingCourses.map(c => c.xspjid))}
            >
              全选待评教
            </Button>
            <Button
              onClick={() => setSelectedXspjIds([])}
              disabled={selectedXspjIds.length === 0}
            >
              清除选择
            </Button>
            <Button
              type="primary"
              danger={!safetyMode}
              icon={<SafetyCertificateOutlined />}
              onClick={handleBatchEvaluate}
              loading={batchRunning}
              disabled={pendingCourses.length === 0 || selectedXspjIds.length === 0}
            >
              {safetyMode ? '批量预览' : '批量提交'} ({selectedXspjIds.length})
            </Button>
          </Space>
        )}
      </Card>

      {/* 一级：评教任务列表 */}
      <Card
        title={
          <Space>
            <UnorderedListOutlined />
            <span>评教任务</span>
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        <Table
          dataSource={tasks}
          columns={taskColumns}
          rowKey="task_id"
          loading={tasksLoading}
          size="small"
          pagination={false}
          rowClassName={(record) => selectedTask?.task_id === record.task_id ? 'ant-table-row-selected' : ''}
          locale={{ emptyText: '暂无评教任务' }}
        />
      </Card>

      {/* 二级：课程列表（选中任务后展示） */}
      {selectedTask && (
        <Card
          title={
            <Space>
              <LeftOutlined style={{ cursor: 'pointer' }} onClick={() => { setSelectedTask(null); setCourses([]); }} />
              <Badge count={pendingCourses.length} offset={[6, 0]}>
                <ExclamationCircleOutlined style={{ fontSize: 18 }} />
              </Badge>
              <span>{selectedTask.task_name} - 课程列表</span>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          <Spin spinning={coursesLoading}>
            {/* 待评教 */}
            {pendingCourses.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <Title level={5} style={{ marginBottom: 8 }}>
                  待评教课程 ({pendingCourses.length})
                </Title>
                <Table
                  dataSource={pendingCourses}
                  columns={pendingColumns}
                  rowKey="xspjid"
                  size="small"
                  pagination={false}
                  rowSelection={{
                    selectedRowKeys: selectedXspjIds,
                    onChange: (keys) => setSelectedXspjIds(keys),
                  }}
                  locale={{ emptyText: '暂无待评教课程' }}
                />
              </div>
            )}

            {/* 已评教 */}
            {completedCourses.length > 0 && (
              <div>
                <Title level={5} style={{ marginBottom: 8 }}>
                  已评教课程 ({completedCourses.length})
                </Title>
                <Table
                  dataSource={completedCourses}
                  columns={completedColumns}
                  rowKey="xspjid"
                  size="small"
                  pagination={false}
                  locale={{ emptyText: '暂无已评教课程' }}
                />
              </div>
            )}

            {!coursesLoading && courses.length === 0 && (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Text type="secondary">该任务下没有课程</Text>
              </div>
            )}
          </Spin>
        </Card>
      )}

      {/* ── 详情弹窗（已评课程） ──────────────────────────────────────────── */}
      <Modal
        title="评教详情"
        open={detailModalVisible}
        onCancel={() => { setDetailModalVisible(false); setCurrentDetail(null); }}
        width={900}
        footer={null}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin indicator={<LoadingOutlined style={{ fontSize: 32 }} />} />
          </div>
        ) : currentDetail ? (
          <div>
            <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="指标库名称">{currentDetail.libname || '-'}</Descriptions.Item>
              <Descriptions.Item label="课程">{currentDetail.course_name}</Descriptions.Item>
              <Descriptions.Item label="教师">{currentDetail.teacher_name}</Descriptions.Item>
              <Descriptions.Item label="评价状态">
                <Tag color="green" icon={<CheckCircleOutlined />}>已评</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="总分">{currentDetail.total_score}</Descriptions.Item>
              <Descriptions.Item label="已评均分">
                {currentDetail.avg_score !== undefined && currentDetail.avg_score !== null
                  ? <Text strong style={{ color: '#52c41a' }}>{currentDetail.avg_score}</Text>
                  : <Text type="secondary">-</Text>}
              </Descriptions.Item>
              <Descriptions.Item label="选择型指标">{currentDetail.selection_count} 项</Descriptions.Item>
              <Descriptions.Item label="文本型指标">{currentDetail.text_count} 项</Descriptions.Item>
            </Descriptions>

            {currentDetail.preface && (
              <Alert
                message="评教说明"
                description={currentDetail.preface}
                type="info"
                style={{ marginBottom: 16 }}
              />
            )}

            <Table
              dataSource={currentDetail.indicators || []}
              columns={detailIndicatorColumns}
              rowKey="zbid"
              size="small"
              pagination={false}
              scroll={{ y: 400 }}
            />
          </div>
        ) : (
          <Text type="secondary">无数据</Text>
        )}
      </Modal>

      {/* ── 单独评价弹窗 ──────────────────────────────────────────────────── */}
      <Modal
        title={
          <Space>
            <EditOutlined />
            <span>评价课程</span>
          </Space>
        }
        open={evaluateModalVisible}
        onCancel={() => setEvaluateModalVisible(false)}
        width={800}
        confirmLoading={evaluateSubmitting}
        okText={safetyMode ? '预览' : '提交评教'}
        onOk={handleSubmitEvaluate}
        bodyStyle={{ maxHeight: '70vh', overflow: 'auto' }}
      >
        {evaluateLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin indicator={<LoadingOutlined style={{ fontSize: 32 }} />} />
          </div>
        ) : evaluateCourse ? (
          <div>
            <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="课程">{evaluateCourse.course_name}</Descriptions.Item>
              <Descriptions.Item label="教师">{evaluateCourse.teacher_name}</Descriptions.Item>
              <Descriptions.Item label="开课单位">{evaluateCourse.department || '-'}</Descriptions.Item>
              <Descriptions.Item label="课程属性">{evaluateCourse.course_type_name || '-'}</Descriptions.Item>
            </Descriptions>

            <Alert
              message="评分提示"
              description={
                <div>
                  <div>1. 选择型指标请点击对应的评分等级</div>
                  <div>2. 文本型指标可填写评价内容（选填可不填）</div>
                  <div>3. 评价选项不能全部相同，否则会被系统拦截</div>
                  <div>4. 可使用下方快捷按钮一键填充</div>
                </div>
              }
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <Space style={{ marginBottom: 16 }}>
              <Button size="small" onClick={() => handleApplyStrategy('highest')}>
                最高分填充
              </Button>
              <Button size="small" danger onClick={() => handleApplyStrategy('lowest')}>
                最低分填充
              </Button>
            </Space>

            <div>
              {evaluateIndicators.map((ind, idx) => (
                <div
                  key={ind.zbid}
                  style={{
                    marginBottom: 12,
                    padding: 12,
                    background: '#fafafa',
                    borderRadius: 8,
                    border: '1px solid #f0f0f0',
                  }}
                >
                  <div style={{ marginBottom: 8, fontWeight: 500 }}>
                    {idx + 1}. {ind.zbmc}
                    {ind.sfbt === 1 && <Tag color="red" style={{ marginLeft: 8 }}>必填</Tag>}
                    {ind.evaltype === 1 ? <Tag style={{ marginLeft: 8 }}>选择</Tag> : <Tag style={{ marginLeft: 8 }}>文本</Tag>}
                    {ind.weight > 0 && <Text type="secondary" style={{ marginLeft: 8 }}>权重 {ind.weight}%</Text>}
                  </div>

                  {ind.evaltype === 1 ? (
                    <Radio.Group
                      value={
                        Array.isArray(ind._customDfdj)
                          ? ind._customDfdj[0]
                          : ind._customDfdj
                      }
                      onChange={(e) => {
                        const val = e.target.value;
                        if (ind.sfdx === 1) {
                          handleScoreChange(ind.zbid, [val, val === 6 ? 5 : val + 1]);
                        } else {
                          handleScoreChange(ind.zbid, val);
                        }
                      }}
                    >
                      <Space wrap>
                        {SCORE_OPTIONS.map(opt => (
                          <Radio.Button key={opt.value} value={opt.value}>
                            {opt.label}
                          </Radio.Button>
                        ))}
                      </Space>
                    </Radio.Group>
                  ) : (
                    <TextArea
                      rows={2}
                      placeholder="请输入评价内容（选填）"
                      value={ind._customResult}
                      onChange={(e) => handleResultChange(ind.zbid, e.target.value)}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <Text type="secondary">无数据</Text>
        )}
      </Modal>

      {/* ── 批量进度弹窗 ──────────────────────────────────────────────────── */}
      <Modal
        title="批量评教进度"
        open={progressModalVisible}
        onCancel={handleCloseProgress}
        footer={
          <Button onClick={handleCloseProgress} disabled={batchRunning}>
            {batchRunning ? '提交中...' : '关闭'}
          </Button>
        }
        closable={!batchRunning}
        maskClosable={!batchRunning}
        width={600}
      >
        <div style={{ marginBottom: 16 }}>
          <Progress
            percent={progressTotal > 0 ? Math.round((progressCurrent / progressTotal) * 100) : 0}
            status={batchRunning ? 'active' : 'success'}
            format={() => `${progressCurrent} / ${progressTotal}`}
          />
          {batchRunning && progressCourseName && (
            <div style={{ textAlign: 'center', marginTop: 8 }}>
              <Text type="secondary">
                正在提交：<Text strong>{progressCourseName}</Text>
              </Text>
            </div>
          )}
        </div>

        <List
          size="small"
          bordered
          dataSource={progressResults}
          renderItem={(item) => (
            <List.Item>
              <Space>
                {item.success
                  ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                  : <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                }
                <Text>{item.course_name}</Text>
                <Text type="secondary">{item.teacher_name}</Text>
                {item.avg_score !== undefined && item.avg_score !== null && (
                  <Tag color="blue">均分 {item.avg_score}</Tag>
                )}
                <Text type={item.success ? 'secondary' : 'danger'}>{item.message}</Text>
              </Space>
            </List.Item>
          )}
          style={{ maxHeight: 300, overflow: 'auto' }}
          locale={{ emptyText: '暂无提交记录' }}
        />

        {!batchRunning && progressResults.length > 0 && (
          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Text strong>
              完成：成功 {progressResults.filter(r => r.success).length} / {progressResults.length}
            </Text>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default EvaluationPage;
