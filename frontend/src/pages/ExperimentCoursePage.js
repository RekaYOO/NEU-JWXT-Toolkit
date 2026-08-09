import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card, Table, Tag, Button, message, Spin, Modal, Descriptions,
  Space, Popconfirm, Typography, Empty, Alert, Grid, Collapse
} from 'antd';
import {
  ExperimentOutlined, CheckCircleOutlined, ClockCircleOutlined,
  TeamOutlined, CalendarOutlined, EnvironmentOutlined,
  UserOutlined, ExclamationCircleOutlined, ReloadOutlined
} from '@ant-design/icons';
import { getExperimentCourses, getExperimentRounds, selectExperimentRound, deselectExperimentRound } from '../services/api';
import './ExperimentCoursePage.css';

const { Title, Text } = Typography;
const { useBreakpoint } = Grid;

// 状态标签
const StatusTag = ({ isSelected, isComplete, mustDoCount, selectedCount }) => {
  if (isComplete) {
    return <Tag color="success" icon={<CheckCircleOutlined />}>已完成</Tag>;
  }
  if (selectedCount > 0) {
    return <Tag color="processing" icon={<ClockCircleOutlined />}>进行中 ({selectedCount}/{mustDoCount})</Tag>;
  }
  return <Tag color="warning" icon={<ExclamationCircleOutlined />}>待选课</Tag>;
};

const ExperimentCoursePage = () => {
  const navigate = useNavigate();
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [term, setTerm] = useState('');
  const [selectedCourse, setSelectedCourse] = useState(null);
  const [selectedProject, setSelectedProject] = useState(null);
  const [rounds, setRounds] = useState([]);
  const [roundsLoading, setRoundsLoading] = useState(false);
  const [roundsModalVisible, setRoundsModalVisible] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [coursesError, setCoursesError] = useState('');
  const actionLockRef = useRef(false);
  const screens = useBreakpoint();
  const isMobile = !screens.md;

  // 加载课程列表
  const loadCourses = async () => {
    setLoading(true);
    setCoursesError('');
    try {
      const data = await getExperimentCourses();
      setCourses(data.courses || []);
      setTerm(data.term || '');
    } catch (error) {
      console.error('加载实验课程失败:', error);
      setCoursesError(error.response?.data?.detail || '实验课程读取失败，请重试；这不代表当前没有实验课程');
    } finally {
      setLoading(false);
    }
  };

  // 初始加载
  useEffect(() => {
    loadCourses();
  }, []);

  // 查看实验班
  const handleViewRounds = async (course, project) => {
    setSelectedCourse(course);
    setSelectedProject(project);
    setRoundsModalVisible(true);
    setRoundsLoading(true);
    
    try {
      const data = await getExperimentRounds(
        course.task_id,
        course.course_no,
        project.project_code,
        term
      );
      setRounds(data.rounds || []);
    } catch (error) {
      console.error('加载实验班失败:', error);
      message.error('加载实验班失败');
    } finally {
      setRoundsLoading(false);
    }
  };

  // 选课
  const performSelect = async (round) => {
    if (!actionLockRef.current) actionLockRef.current = true;
    setActionLoading(true);
    try {
      const result = await selectExperimentRound({
        term,
        task_id: selectedCourse.task_id,
        project_code: selectedProject.project_code,
        round_id: round.wid,
      });
      
      if (result.code === '0') {
        message.success('选课成功');
        setRoundsModalVisible(false);
        loadCourses(); // 刷新课程列表
      } else {
        message.error(result.msg || '选课失败');
      }
    } catch (error) {
      console.error('选课失败:', error);
      message.error('选课失败');
    } finally {
      setActionLoading(false);
      actionLockRef.current = false;
    }
  };

  const handleSelect = (round) => {
    if (actionLockRef.current) return;
    actionLockRef.current = true;
    const unknown = round.conflict_status === 'unknown';
    Modal.confirm({
      title: unknown ? '课表冲突尚未完全核验' : '确认选择实验班？',
      content: unknown
        ? '个人课表缓存陈旧、缺失或该实验班时间信息不完整。继续后仍以教务系统的最终校验结果为准。'
        : `即将选择“${round.round_name || selectedProject?.project_name || '该实验班'}”，提交后请以教务系统返回结果为准。`,
      okText: unknown ? '仍要选择' : '确认选择',
      cancelText: '取消',
      onOk: () => performSelect(round),
      onCancel: () => { actionLockRef.current = false; },
    });
  };

  // 退课
  const handleDeselect = async (project) => {
    if (actionLockRef.current) return;
    actionLockRef.current = true;
    setActionLoading(true);
    try {
      const result = await deselectExperimentRound({
        term,
        task_id: project.task_id,
        project_code: project.project_code,
        round_id: project.selected_round_id,
      });
      
      if (result.code === '0') {
        message.success('退课成功');
        loadCourses(); // 刷新课程列表
      } else {
        message.error(result.msg || '退课失败');
      }
    } catch (error) {
      console.error('退课失败:', error);
      message.error('退课失败');
    } finally {
      setActionLoading(false);
      actionLockRef.current = false;
    }
  };

  // 课程表格列
  const courseColumns = [
    {
      title: '课程名称',
      dataIndex: 'course_name',
      key: 'course_name',
      width: 250,
      render: (text, record) => (
        <div>
          <div className="course-name">{text}</div>
          <div className="course-no">{record.course_no}</div>
        </div>
      ),
    },
    {
      title: '学分/学时',
      key: 'credit',
      width: 100,
      render: (_, record) => (
        <span>{record.credit}学分 / {record.experiment_hours}学时</span>
      ),
    },
    {
      title: '实验中心',
      dataIndex: 'center_name',
      key: 'center_name',
      width: 180,
      ellipsis: true,
    },
    {
      title: '必做项目',
      key: 'projects_status',
      width: 120,
      render: (_, record) => (
        <span>{record.selected_count} / {record.must_do_count}</span>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 140,
      render: (_, record) => (
        <StatusTag
          isComplete={record.is_complete}
          mustDoCount={record.must_do_count}
          selectedCount={record.selected_count}
        />
      ),
    },
  ];

  // 展开行：显示实验项目列表
  const expandedRowRender = (course) => {
    return (
      <div className="projects-container">
        <div className="projects-header">实验项目列表</div>
        <div className="projects-list">
          {course.projects.map((project, index) => (
            <div key={index} className={`project-item ${project.is_selected ? 'selected' : ''}`}>
              <div className="project-info">
                <div className="project-name">
                  {project.must_do && <Tag color="red" size="small">必做</Tag>}
                  <span>{project.project_name}</span>
                </div>
                <div className="project-code">{project.project_code}</div>
              </div>
              <div className="project-status">
                {project.is_selected ? (
                  <Space>
                    <Tag color="success" icon={<CheckCircleOutlined />}>
                      已选: {project.select_status}
                    </Tag>
                    <Popconfirm
                      title="确认退课？"
                      description="退课后需要重新选择实验班"
                      onConfirm={() => handleDeselect({ ...project, task_id: course.task_id })}
                      okText="确认"
                      cancelText="取消"
                    >
                      <Button size="small" danger loading={actionLoading}>
                        退课
                      </Button>
                    </Popconfirm>
                  </Space>
                ) : (
                  <Button
                    type="primary"
                    size="small"
                    onClick={() => handleViewRounds(course, project)}
                  >
                    选课
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  // 统计信息
  const stats = useMemo(() => {
    const total = courses.length;
    const complete = courses.filter(c => c.is_complete).length;
    const inProgress = courses.filter(c => c.selected_count > 0 && !c.is_complete).length;
    const pending = courses.filter(c => c.selected_count === 0).length;
    return { total, complete, inProgress, pending };
  }, [courses]);
  const orderedCourses = useMemo(() => [...courses].sort((left, right) => (
    Number(left.is_complete) - Number(right.is_complete)
    || (right.must_do_count - right.selected_count) - (left.must_do_count - left.selected_count)
    || left.course_name.localeCompare(right.course_name, 'zh-CN')
  )), [courses]);
  const orderedRounds = useMemo(() => [...rounds].sort((left, right) => {
    const rank = round => {
      if (round.can_select && round.conflict_status === 'clear') return 0;
      if (round.can_select && round.conflict_status === 'unknown') return 1;
      if (round.conflict_status === 'conflict') return 2;
      return 3;
    };
    return rank(left) - rank(right) || left.selected_count - right.selected_count;
  }), [rounds]);

  if (loading) {
    return (
      <div className="loading-container">
        <Spin size="large" tip="加载实验选课..." />
      </div>
    );
  }

  return (
    <div className="experiment-course-page">
      {/* 页面标题 */}
      <div className="page-header">
        <div className="experiment-page-heading">
          <Title level={4}>
            <ExperimentOutlined /> 实验选课
          </Title>
          <Text type="secondary">{term} 学期</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={loadCourses} size="small">
          刷新
        </Button>
      </div>

      {/* 统计卡片 */}
      <div className="stats-row">
        <Card className="stat-card">
          <div className="stat-value">{stats.total}</div>
          <div className="stat-label">实验课程</div>
        </Card>
        <Card className="stat-card success">
          <div className="stat-value">{stats.complete}</div>
          <div className="stat-label">已完成</div>
        </Card>
        <Card className="stat-card warning">
          <div className="stat-value">{stats.inProgress}</div>
          <div className="stat-label">进行中</div>
        </Card>
        <Card className="stat-card error">
          <div className="stat-value">{stats.pending}</div>
          <div className="stat-label">待选课</div>
        </Card>
      </div>

      {/* 提示信息 */}
      <Alert
        message="使用说明"
        description={isMobile
          ? '展开课程即可选班或退课，红色标签表示必做项目。'
          : '点击课程展开查看实验项目，已选项目可以退课，未选项目点击选课按钮选择实验班。红色标签为必做项目。'}
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />

      {coursesError && (
        <Alert
          type="error"
          showIcon
          message={coursesError}
          action={<Button size="small" onClick={loadCourses}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 课程列表 */}
      <Card className="courses-card">
        {courses.length > 0 ? (
          isMobile ? (
            <Collapse
              className="mobile-course-collapse"
              accordion
              expandIconPosition="end"
              items={orderedCourses.map(course => ({
                key: course.task_id,
                label: (
                  <div className="mobile-course-summary">
                    <div className="mobile-course-title">
                      <strong>{course.course_name}</strong>
                      <span>{course.course_no} · {course.credit} 学分</span>
                    </div>
                    <StatusTag
                      isComplete={course.is_complete}
                      mustDoCount={course.must_do_count}
                      selectedCount={course.selected_count}
                    />
                  </div>
                ),
                children: expandedRowRender(course),
              }))}
            />
          ) : (
            <Table
              columns={courseColumns}
              dataSource={orderedCourses}
              rowKey="task_id"
              expandable={{ expandedRowRender }}
              pagination={false}
              size="middle"
            />
          )
        ) : (
          <Empty description={coursesError ? '实验课程读取失败，重试后显示' : '暂无实验课程'} />
        )}
      </Card>

      {/* 实验班选择弹窗 */}
      <Modal
        title="选择实验班"
        rootClassName="experiment-rounds-modal"
        open={roundsModalVisible}
        onCancel={() => setRoundsModalVisible(false)}
        footer={null}
        width={isMobile ? 'calc(100vw - 16px)' : 800}
        styles={{ body: { maxHeight: isMobile ? 'calc(100dvh - 120px)' : 600, overflowY: 'auto' } }}
      >
        {roundsLoading ? (
          <div className="modal-loading">
            <Spin tip="加载实验班..." />
          </div>
        ) : rounds.length > 0 ? (
          <div className="rounds-list">
            {selectedProject && (
              <div className="project-info-header">
                <Text strong>{selectedProject.project_name}</Text>
                <Text type="secondary">{selectedProject.project_code}</Text>
              </div>
            )}
            {orderedRounds.map((round) => (
              <Card
                key={round.wid}
                size="small"
                className={`round-card ${round.conflict ? 'conflict' : ''} ${round.is_full ? 'full' : ''}`}
              >
                <div className="round-header">
                  <div className="round-name">{round.round_name}</div>
                  <div className="round-capacity">
                    <TeamOutlined /> {round.selected_count} / {round.capacity}
                    {round.is_full && <Tag color="error" size="small">已满</Tag>}
                    {round.conflict_status === 'conflict' && <Tag color="error" size="small">时间冲突</Tag>}
                    {round.conflict_status === 'unknown' && <Tag color="warning" size="small">待核验</Tag>}
                  </div>
                </div>
                <Descriptions size="small" column={isMobile ? 1 : 2}>
                  <Descriptions.Item label="教师">{round.teacher}</Descriptions.Item>
                  <Descriptions.Item label="时间">
                    <CalendarOutlined /> {round.week} {round.day} {round.time}
                  </Descriptions.Item>
                  <Descriptions.Item label="地点">
                    <EnvironmentOutlined /> {round.location || '待定'}
                  </Descriptions.Item>
                  <Descriptions.Item label="选课时间">
                    {round.select_start?.slice(0, 16)} ~ {round.select_end?.slice(0, 16)}
                  </Descriptions.Item>
                </Descriptions>
                {round.conflicts?.length > 0 && (
                  <Alert
                    type="error"
                    showIcon
                    message={`与 ${round.conflicts.map(item => item.course_name).join('、')} 冲突`}
                    description={round.conflicts.map(item => (
                      `${item.overlapping_weeks?.length ? `${item.overlapping_weeks.join('、')}周` : '周次待确认'} · ${item.reason}`
                    )).join('；')}
                    className="experiment-conflict-detail"
                  />
                )}
                {round.conflict_status === 'unknown' && (
                  <Alert type="warning" showIcon message="无法完整核验个人课表，选择前会再次确认" className="experiment-conflict-detail" />
                )}
                <div className="round-action">
                  {round.weekday > 0 && (
                    <Button onClick={() => navigate(`/timetable?term=${encodeURIComponent(term)}&week=${round.weeks?.[0] || ''}&day=${round.weekday}`)}>
                      在课表中查看
                    </Button>
                  )}
                  <Button
                    type="primary"
                    disabled={!round.can_select}
                    onClick={() => handleSelect(round)}
                    loading={actionLoading}
                  >
                    {round.disabled_reason || (round.conflict_status === 'unknown' ? '确认后选择' : '选择此班')}
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <Empty description="暂无可选实验班" />
        )}
      </Modal>
    </div>
  );
};

export default ExperimentCoursePage;
