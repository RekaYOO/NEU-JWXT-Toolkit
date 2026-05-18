import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Tag, Space, Modal, Descriptions, Spin,
  message, Alert, Tooltip, Steps, Progress, InputNumber, Select,
  Collapse, Typography, Divider, Switch, Popconfirm, Badge,
} from 'antd';
import {
  StarOutlined, StarFilled, EyeOutlined, ThunderboltOutlined,
  SafetyCertificateOutlined, ExclamationCircleOutlined,
  CheckCircleOutlined, CloseCircleOutlined, TrophyOutlined,
  LoadingOutlined, LeftOutlined, UnorderedListOutlined,
} from '@ant-design/icons';
import {
  getEvaluationTasks, getEvaluationCourses, getEvaluationIndicators,
  submitEvaluation, batchEvaluation,
} from '../services/api';
import './EvaluationPage.css';

const { Text, Title } = Typography;
const { Panel } = Collapse;

// dfdj 评分等级映射
const SCORE_MAP = { 6: 100, 5: 90, 4: 80, 3: 70, 2: 60, 1: 50 };
const SCORE_LABELS = {
  6: '优秀 (100)', 5: '很好 (90)', 4: '好 (80)',
  3: '较好 (70)', 2: '一般 (60)', 1: '较差 (50)',
};

const EvaluationPage = () => {
  // ── 状态 ────────────────────────────────────────────────────────────────
  const [tasks, setTasks] = useState([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [selectedTask, setSelectedTask] = useState(null); // 当前选中的一级任务
  const [courses, setCourses] = useState([]);
  const [coursesLoading, setCoursesLoading] = useState(false);
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [currentDetail, setCurrentDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewModalVisible, setPreviewModalVisible] = useState(false);

  // 全局策略
  const [globalStrategy, setGlobalStrategy] = useState('highest');

  // 批量评教
  const [selectedXspjIds, setSelectedXspjIds] = useState([]);
  const [batchRunning, setBatchRunning] = useState(false);

  // 安全开关
  const [safetyMode, setSafetyMode] = useState(true);

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

  // ── 查看指标详情 ───────────────────────────────────────────────────────
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
      });
    } catch (error) {
      message.error('获取评教指标失败');
    } finally {
      setDetailLoading(false);
    }
  };

  // ── 预览评分 ───────────────────────────────────────────────────────────
  const handlePreview = async (record, strategy) => {
    setPreviewLoading(true);
    setPreviewModalVisible(true);
    setPreviewData(null);
    try {
      const data = await submitEvaluation(record.task_id, record.xspjid, strategy || globalStrategy, null, true);
      setPreviewData(data);
    } catch (error) {
      message.error('预览评分失败');
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── 单个提交（安全模式：仅预览） ────────────────────────────────────────
  const handleSubmitOne = async (record, strategy) => {
    if (safetyMode) {
      handlePreview(record, strategy);
      return;
    }

    Modal.confirm({
      title: '确认提交评教',
      content: `即将提交：${record.course_name} - ${record.teacher_name}。评教系统不支持重试！`,
      okText: '确认提交',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await submitEvaluation(record.task_id, record.xspjid, strategy || globalStrategy, null, false);
          if (result.success) {
            message.success(`${record.course_name} 评教提交成功`);
            // 刷新课程列表
            if (selectedTask) handleSelectTask(selectedTask);
          } else {
            message.error(`提交失败: ${result.message}`);
          }
        } catch (error) {
          message.error('提交异常');
        }
      },
    });
  };

  // ── 批量评教 ────────────────────────────────────────────────────────────
  const handleBatchEvaluate = () => {
    if (!selectedTask) {
      message.warning('请先选择评教任务');
      return;
    }
    const pendingCourses = courses.filter(c => !c.is_evaluated);

    // 强制要求勾选课程，未勾选不能提交
    if (selectedXspjIds.length === 0) {
      message.warning('请先勾选至少一门待评课程');
      return;
    }
    const targetXspjIds = selectedXspjIds;

    if (safetyMode) {
      Modal.info({
        title: '安全模式 - 批量预览',
        content: `将预览 ${targetXspjIds.length} 门课程的评分方案，不会实际提交。`,
        onOk: async () => {
          setBatchRunning(true);
          try {
            const data = await batchEvaluation(selectedTask.task_id, globalStrategy, null, true, targetXspjIds);
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

    // 非安全模式：实际批量提交
    Modal.confirm({
      title: '确认批量提交评教',
      content: `将对 ${targetXspjIds.length} 门课程提交评教。评教系统不支持重试！`,
      okText: '确认提交',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        setBatchRunning(true);
        try {
          const data = await batchEvaluation(selectedTask.task_id, globalStrategy, null, false, targetXspjIds);
          const successCount = data.success_count || 0;
          message.info(`批量评教完成：成功 ${successCount}/${data.total}`);
          // 刷新
          handleSelectTask(selectedTask);
          loadTasks();
        } catch (error) {
          message.error('批量评教异常');
        } finally {
          setBatchRunning(false);
        }
      },
    });
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

  // ── 二级课程列 ──────────────────────────────────────────────────────────
  const courseColumns = [
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
      render: (val) => val
        ? <Tag color="green" icon={<CheckCircleOutlined />}>已评</Tag>
        : <Tag color="orange" icon={<ExclamationCircleOutlined />}>未评</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_, record) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => handleViewDetail(record)}
          >
            指标
          </Button>
          {!record.is_evaluated && (
            <>
              <Button
                size="small"
                type="primary"
                icon={<TrophyOutlined />}
                onClick={() => handlePreview(record, 'highest')}
              >
                最高分预览
              </Button>
              <Button
                size="small"
                danger
                icon={<SafetyCertificateOutlined />}
                onClick={() => handleSubmitOne(record, globalStrategy)}
              >
                {safetyMode ? '预览' : '提交'}
              </Button>
            </>
          )}
        </Space>
      ),
    },
  ];

  // ── 指标表格列定义 ──────────────────────────────────────────────────────
  const indicatorColumns = [
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
      title: '选项',
      dataIndex: 'level_json',
      key: 'level_json',
      width: 200,
      ellipsis: true,
      render: (levelJson) => {
        if (!levelJson || !Array.isArray(levelJson) || levelJson.length === 0) return '-';
        return levelJson.map(opt => {
          const label = opt.mc || opt.name || '';
          const val = opt.df || opt.value || '';
          return `${label}(${val})`;
        }).join('、');
      },
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
              { value: 'custom', label: '自定义分数' },
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
                  columns={courseColumns}
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
                  columns={courseColumns.filter(c => c.key !== 'action').concat([{
                    title: '操作',
                    key: 'action',
                    width: 80,
                    render: (_, record) => (
                      <Button size="small" icon={<EyeOutlined />}
                        onClick={() => handleViewDetail(record)}>
                        指标
                      </Button>
                    ),
                  }])}
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

      {/* ── 指标详情弹窗 ──────────────────────────────────────────────────── */}
      <Modal
        title="评教指标体系"
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
                {currentDetail.is_evaluated
                  ? <Tag color="green" icon={<CheckCircleOutlined />}>已评</Tag>
                  : <Tag color="orange" icon={<ExclamationCircleOutlined />}>未评</Tag>}
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
              columns={indicatorColumns}
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

      {/* ── 预览弹窗 ──────────────────────────────────────────────────────── */}
      <Modal
        title="评分预览"
        open={previewModalVisible}
        onCancel={() => { setPreviewModalVisible(false); setPreviewData(null); }}
        width={700}
        footer={
          <Space>
            <Button onClick={() => setPreviewModalVisible(false)}>关闭</Button>
            {!safetyMode && previewData?.validation?.valid && (
              <Popconfirm
                title="确认提交？评教系统不支持重试！"
                onConfirm={async () => {
                  try {
                    const data = previewData;
                    const result = await submitEvaluation(
                      data._taskId, data._xspjid, data.strategy || globalStrategy, null, false
                    );
                    if (result.success) {
                      message.success('提交成功');
                      setPreviewModalVisible(false);
                      if (selectedTask) handleSelectTask(selectedTask);
                    } else {
                      message.error(`提交失败: ${result.message}`);
                    }
                  } catch (e) {
                    message.error('提交异常');
                  }
                }}
              >
                <Button type="primary" danger>确认提交</Button>
              </Popconfirm>
            )}
          </Space>
        }
      >
        {previewLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin indicator={<LoadingOutlined style={{ fontSize: 32 }} />} />
          </div>
        ) : previewData ? (
          <div>
            <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="课程">{previewData.course_name}</Descriptions.Item>
              <Descriptions.Item label="教师">{previewData.teacher_name}</Descriptions.Item>
              <Descriptions.Item label="策略">
                {previewData.strategy === 'highest' ? '最高分' :
                 previewData.strategy === 'lowest' ? '最低分' : '自定义'}
              </Descriptions.Item>
              <Descriptions.Item label="模式">
                {previewData.dry_run ? <Tag color="blue">预览</Tag> : <Tag color="red">实际提交</Tag>}
              </Descriptions.Item>
            </Descriptions>

            {/* 验证结果 */}
            {previewData.validation && (
              <Alert
                message={previewData.validation.valid ? '评分验证通过' : '评分验证失败'}
                description={
                  previewData.validation.errors?.length > 0
                    ? previewData.validation.errors.join('；')
                    : '所有必填项已填写，分数分布合理'
                }
                type={previewData.validation.valid ? 'success' : 'error'}
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}

            {previewData.dry_run && (
              <Alert
                message="安全模式 - 仅预览"
                description="当前为安全模式，未实际提交。关闭安全模式后方可提交。"
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}

            <Title level={5}>评分详情</Title>
            <Table
              dataSource={previewData.indicators || []}
              columns={previewIndicatorColumns}
              rowKey="zbid"
              size="small"
              pagination={false}
              scroll={{ y: 300 }}
            />
          </div>
        ) : (
          <Text type="secondary">无预览数据</Text>
        )}
      </Modal>
    </div>
  );
};

export default EvaluationPage;
