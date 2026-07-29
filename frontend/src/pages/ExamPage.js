import React, { useState, useEffect, useRef } from 'react';
import {
  Card, Select, Button, Tag, Empty, Spin, Alert, Statistic, Row, Col,
  Timeline, Tooltip, Badge, message
} from 'antd';
import {
  CalendarOutlined, ExportOutlined, ClockCircleOutlined,
  EnvironmentOutlined, UserOutlined, BookOutlined,
  CheckCircleOutlined, HourglassOutlined, FlagOutlined,
  FileTextOutlined
} from '@ant-design/icons';
import { getExamTerms, getExams, exportExamsICS } from '../services/api';
import './ExamPage.css';

const { Option } = Select;

const STATUS_MAP = {
  0: { label: '待考', color: 'blue', icon: <HourglassOutlined /> },
  1: { label: '进行中', color: 'orange', icon: <ClockCircleOutlined /> },
  2: { label: '已结束', color: 'default', icon: <CheckCircleOutlined /> },
};

const ExamPage = () => {
  const [terms, setTerms] = useState([]);
  const [currentTerm, setCurrentTerm] = useState('');
  const [selectedTerm, setSelectedTerm] = useState('');
  const [exams, setExams] = useState([]);
  const [stats, setStats] = useState({ total: 0, upcoming: 0, ongoing: 0, finished: 0 });
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState(null);
  const examRequestGeneration = useRef(0);

  // 加载学期列表
  useEffect(() => {
    const loadTerms = async () => {
      try {
        const data = await getExamTerms();
        setTerms(data.terms || []);
        setCurrentTerm(data.current || '');
        setSelectedTerm(data.current || '');
      } catch (e) {
        setError('获取学期列表失败');
      }
    };
    loadTerms();
  }, []);

  // 加载考试列表
  useEffect(() => {
    if (!selectedTerm) return;
    const generation = ++examRequestGeneration.current;
    const loadExams = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getExams(selectedTerm);
        if (generation !== examRequestGeneration.current) return;
        setExams(data.exams || []);
        setStats({
          total: data.total || 0,
          upcoming: data.upcoming || 0,
          ongoing: data.ongoing || 0,
          finished: data.finished || 0,
        });
      } catch (e) {
        if (generation !== examRequestGeneration.current) return;
        setError('获取考试安排失败');
        message.error('获取考试安排失败');
      } finally {
        if (generation === examRequestGeneration.current) {
          setLoading(false);
        }
      }
    };
    loadExams();
  }, [selectedTerm]);

  const handleExportICS = async () => {
    setExporting(true);
    try {
      const blob = await exportExamsICS(selectedTerm);
      const url = window.URL.createObjectURL(new Blob([blob], { type: 'text/calendar' }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `exams_${selectedTerm}.ics`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      message.success('ICS 文件已导出');
    } catch (e) {
      message.error('导出 ICS 失败');
    } finally {
      setExporting(false);
    }
  };

  const groupedExams = exams.reduce((acc, exam) => {
    const status = exam.exam_status;
    if (!acc[status]) acc[status] = [];
    acc[status].push(exam);
    return acc;
  }, {});

  const renderExamCard = (exam) => {
    const status = STATUS_MAP[exam.exam_status] || STATUS_MAP[0];
    return (
      <Card
        key={exam.task_id}
        className={`exam-card exam-status-${exam.exam_status}`}
        size="small"
      >
        <div className="exam-card-header">
          <div className="exam-course-name">
            <BookOutlined /> {exam.course_name}
          </div>
          <Tag color={status.color} icon={status.icon}>
            {status.label}
          </Tag>
        </div>
        <div className="exam-card-body">
          <div className="exam-info-row">
            <Tooltip title="考试时间">
              <span className="exam-info-item">
                <ClockCircleOutlined /> {exam.exam_time_description}
              </span>
            </Tooltip>
          </div>
          <div className="exam-info-row">
            <Tooltip title="考场">
              <span className="exam-info-item">
                <EnvironmentOutlined /> {exam.exam_place || '待定'}
              </span>
            </Tooltip>
            <Tooltip title="座位号">
              <span className="exam-info-item">
                <FlagOutlined /> 座位 {exam.exam_seat_no || '待定'}
              </span>
            </Tooltip>
          </div>
          <div className="exam-info-row">
            <Tooltip title="任课教师">
              <span className="exam-info-item">
                <UserOutlined /> {exam.teachers || '—'}
              </span>
            </Tooltip>
            <Tooltip title="考试类型">
              <span className="exam-info-item">
                <FileTextOutlined /> {exam.exam_type}
              </span>
            </Tooltip>
          </div>
        </div>
      </Card>
    );
  };

  return (
    <div className="exam-page">
      <div className="exam-page-header">
        <h2><CalendarOutlined /> 我的考试</h2>
        <div className="exam-header-actions">
          <Select
            value={selectedTerm}
            onChange={setSelectedTerm}
            style={{ width: 220 }}
            placeholder="选择学期"
            loading={terms.length === 0}
          >
            {terms.map(t => (
              <Option key={t.item_code} value={t.item_code}>
                {t.item_name} {t.selected ? '(当前)' : ''}
              </Option>
            ))}
          </Select>
          <Button
            type="primary"
            icon={<ExportOutlined />}
            onClick={handleExportICS}
            loading={exporting}
            disabled={exams.length === 0}
          >
            导出 ICS
          </Button>
        </div>
      </div>

      {error && (
        <Alert message={error} type="error" showIcon style={{ marginBottom: 16 }} />
      )}

      <Row gutter={16} className="exam-stats-row">
        <Col xs={12} sm={6}>
          <Card className="stat-card">
            <Statistic
              title="总考试"
              value={stats.total}
              prefix={<CalendarOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className="stat-card stat-upcoming">
            <Statistic
              title="待考"
              value={stats.upcoming}
              valueStyle={{ color: '#1890ff' }}
              prefix={<HourglassOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className="stat-card stat-ongoing">
            <Statistic
              title="进行中"
              value={stats.ongoing}
              valueStyle={{ color: '#fa8c16' }}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card className="stat-card stat-finished">
            <Statistic
              title="已结束"
              value={stats.finished}
              valueStyle={{ color: '#8c8c8c' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Spin spinning={loading} tip="加载中...">
        {exams.length === 0 && !loading && !error ? (
          <Empty description="该学期暂无考试安排" />
        ) : (
          <div className="exam-timeline">
            {/* 待考 */}
            {groupedExams[0] && groupedExams[0].length > 0 && (
              <div className="exam-group">
                <div className="exam-group-title">
                  <Badge color="blue" /> 待考考试 ({groupedExams[0].length})
                </div>
                <div className="exam-cards">
                  {groupedExams[0].map(renderExamCard)}
                </div>
              </div>
            )}

            {/* 进行中 */}
            {groupedExams[1] && groupedExams[1].length > 0 && (
              <div className="exam-group">
                <div className="exam-group-title">
                  <Badge color="orange" /> 进行中 ({groupedExams[1].length})
                </div>
                <div className="exam-cards">
                  {groupedExams[1].map(renderExamCard)}
                </div>
              </div>
            )}

            {/* 已结束 */}
            {groupedExams[2] && groupedExams[2].length > 0 && (
              <div className="exam-group">
                <div className="exam-group-title">
                  <Badge color="default" /> 已结束 ({groupedExams[2].length})
                </div>
                <div className="exam-cards">
                  {groupedExams[2].map(renderExamCard)}
                </div>
              </div>
            )}
          </div>
        )}
      </Spin>
    </div>
  );
};

export default ExamPage;
