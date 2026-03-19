import React, { useState, useEffect, useCallback } from 'react';
import { 
  Card, Table, Select, DatePicker, Button, Tag, Space, 
  Statistic, Row, Col, Alert, Input, message, Spin,
  Radio, Typography, Empty
} from 'antd';
import { 
  FileTextOutlined, DownloadOutlined, DeleteOutlined,
  SearchOutlined, ReloadOutlined, InfoCircleOutlined,
  WarningOutlined, CloseCircleOutlined, CheckCircleOutlined
} from '@ant-design/icons';
import { getLogSummary, getLogFiles, getLogContent, tailLog, searchLogs, cleanupLogs } from '../services/api';
import dayjs from 'dayjs';
import './LogsPage.css';

const { Option } = Select;
const { RangePicker } = DatePicker;
const { Text } = Typography;

// 日志级别颜色映射
const levelColors = {
  'DEBUG': 'default',
  'INFO': 'processing',
  'WARNING': 'warning',
  'ERROR': 'error',
  'CRITICAL': 'red',
  'UNKNOWN': 'default',
};

// 日志分类选项
const categoryOptions = [
  { value: 'system', label: '系统日志', color: 'blue' },
  { value: 'access', label: '访问日志', color: 'green' },
  { value: 'error', label: '错误日志', color: 'red' },
  { value: 'login', label: '登录日志', color: 'purple' },
  { value: 'sync', label: '同步日志', color: 'orange' },
];

const LogsPage = () => {
  // 状态
  const [summary, setSummary] = useState(null);
  const [files, setFiles] = useState([]);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  
  // 查询参数
  const [selectedCategory, setSelectedCategory] = useState('system');
  const [selectedDate, setSelectedDate] = useState(dayjs());
  const [selectedLevel, setSelectedLevel] = useState(null);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [viewMode, setViewMode] = useState('content'); // content / tail / search

  // 加载统计摘要
  const loadSummary = useCallback(async () => {
    try {
      const data = await getLogSummary(7);
      setSummary(data);
    } catch (error) {
      // 静默处理错误，避免弹窗打扰用户
      console.log('加载日志统计失败:', error);
      setSummary({ total_files: 0, total_size_mb: 0, period_days: 7, categories: {} });
    }
  }, []);

  // 加载文件列表
  const loadFiles = useCallback(async () => {
    try {
      const data = await getLogFiles(selectedCategory, 7);
      setFiles(data);
    } catch (error) {
      // 静默处理错误
      console.log('加载日志文件列表失败:', error);
      setFiles([]);
    }
  }, [selectedCategory]);

  // 加载日志内容
  const loadLogContent = useCallback(async () => {
    if (!selectedCategory || !selectedDate) return;
    
    setLoading(true);
    try {
      const dateStr = selectedDate.format('YYYY-MM-DD');
      let data;
      
      if (viewMode === 'tail') {
        data = await tailLog(selectedCategory, dateStr, 100);
      } else {
        data = await getLogContent(
          selectedCategory, 
          dateStr, 
          selectedLevel || undefined,
          searchKeyword || undefined,
          200
        );
      }
      
      setEntries(data.entries || []);
    } catch (error) {
      // 静默处理错误
      console.log('加载日志内容失败:', error);
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [selectedCategory, selectedDate, selectedLevel, searchKeyword, viewMode]);

  // 搜索日志
  const handleSearch = async () => {
    if (!searchKeyword.trim()) {
      message.warning('请输入搜索关键词');
      return;
    }
    
    setSearchLoading(true);
    try {
      const data = await searchLogs(searchKeyword, selectedCategory, 7, 100);
      // 转换为统一的格式
      const formatted = (data.results || []).map(r => ({
        timestamp: r.timestamp || `${r.date} 00:00:00`,
        level: r.level || 'INFO',
        logger: `${r.category}`,
        message: r.message,
      }));
      setEntries(formatted);
      setViewMode('search');
      if (data.total > 0) {
        message.success(`找到 ${data.total} 条记录`);
      }
    } catch (error) {
      // 静默处理错误
      console.log('搜索失败:', error);
    } finally {
      setSearchLoading(false);
    }
  };

  // 清理旧日志
  const handleCleanup = async () => {
    try {
      const data = await cleanupLogs(30);
      if (data.deleted_files > 0) {
        message.success(`已清理 ${data.deleted_files} 个旧日志文件`);
      }
      loadSummary();
      loadFiles();
    } catch (error) {
      // 静默处理错误
      console.log('清理失败:', error);
    }
  };

  // 下载日志
  const handleDownload = () => {
    if (!selectedCategory || !selectedDate) return;
    const dateStr = selectedDate.format('YYYY-MM-DD');
    const url = `/api/logs/download/${selectedCategory}/${dateStr}`;
    window.open(url, '_blank');
  };

  // 初始加载
  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  useEffect(() => {
    if (viewMode !== 'search') {
      loadLogContent();
    }
  }, [loadLogContent, viewMode]);

  // 表格列定义
  const columns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 180,
      render: (text) => text || '-',
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 100,
      render: (level) => (
        <Tag color={levelColors[level?.toUpperCase()] || 'default'}>
          {level?.toUpperCase() || 'UNKNOWN'}
        </Tag>
      ),
    },
    {
      title: '日志器',
      dataIndex: 'logger',
      key: 'logger',
      width: 200,
      ellipsis: true,
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (text) => <Text code style={{ fontSize: '12px' }}>{text}</Text>,
    },
  ];

  return (
    <div className="logs-page">
      {/* 统计卡片 */}
      {summary && (
        <Row gutter={16} className="stats-row" style={{ marginBottom: 16 }}>
          <Col xs={24} sm={8}>
            <Card>
              <Statistic
                title="日志文件总数"
                value={summary.total_files}
                prefix={<FileTextOutlined />}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card>
              <Statistic
                title="总大小"
                value={summary.total_size_mb}
                suffix="MB"
                precision={2}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card>
              <Statistic
                title="统计周期"
                value={`${summary.period_days}天`}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 日志查看器 */}
      <Card
        title={
          <Space>
            <span>日志查看器</span>
            <Tag color="blue">{categoryOptions.find(c => c.value === selectedCategory)?.label}</Tag>
          </Space>
        }
        extra={
          <Space>
            <Button 
              icon={<DeleteOutlined />} 
              onClick={handleCleanup}
              size="small"
            >
              清理旧日志
            </Button>
            <Button 
              icon={<DownloadOutlined />} 
              onClick={handleDownload}
              size="small"
              type="primary"
            >
              下载当前日志
            </Button>
          </Space>
        }
      >
        {/* 筛选工具栏 */}
        <Space wrap style={{ marginBottom: 16 }}>
          <Select
            value={selectedCategory}
            onChange={setSelectedCategory}
            style={{ width: 150 }}
            placeholder="选择分类"
          >
            {categoryOptions.map(opt => (
              <Option key={opt.value} value={opt.value}>
                <Tag color={opt.color} style={{ marginRight: 4 }}>{opt.label}</Tag>
              </Option>
            ))}
          </Select>

          <DatePicker
            value={selectedDate}
            onChange={setSelectedDate}
            placeholder="选择日期"
          />

          <Select
            value={selectedLevel}
            onChange={setSelectedLevel}
            style={{ width: 120 }}
            placeholder="日志级别"
            allowClear
          >
            <Option value="DEBUG">DEBUG</Option>
            <Option value="INFO">INFO</Option>
            <Option value="WARNING">WARNING</Option>
            <Option value="ERROR">ERROR</Option>
            <Option value="CRITICAL">CRITICAL</Option>
          </Select>

          <Radio.Group 
            value={viewMode} 
            onChange={(e) => setViewMode(e.target.value)}
            buttonStyle="solid"
          >
            <Radio.Button value="content">全部内容</Radio.Button>
            <Radio.Button value="tail">最新100行</Radio.Button>
          </Radio.Group>

          <Button 
            icon={<ReloadOutlined />}
            onClick={() => {
              loadLogContent();
              loadSummary();
            }}
          >
            刷新
          </Button>
        </Space>

        {/* 搜索栏 */}
        <Space style={{ marginBottom: 16, display: 'flex' }}>
          <Input
            placeholder="搜索日志关键词..."
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            onPressEnter={handleSearch}
            style={{ width: 400 }}
            prefix={<SearchOutlined />}
            allowClear
          />
          <Button 
            type="primary" 
            icon={<SearchOutlined />}
            onClick={handleSearch}
            loading={searchLoading}
          >
            搜索
          </Button>
          {viewMode === 'search' && (
            <Button onClick={() => {
              setViewMode('content');
              setSearchKeyword('');
              loadLogContent();
            }}>
              返回查看
            </Button>
          )}
        </Space>

        {/* 日志内容表格 */}
        <Table
          columns={columns}
          dataSource={entries}
          rowKey={(record, index) => `${record.timestamp}-${index}`}
          loading={loading}
          pagination={{
            pageSize: 50,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
          }}
          size="small"
          bordered
          scroll={{ x: 'max-content', y: 500 }}
          locale={{
            emptyText: <Empty description="暂无日志记录" />,
          }}
        />

        {/* 日志文件列表 */}
        {files.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Text type="secondary">最近日志文件：</Text>
            <Space wrap size="small" style={{ marginTop: 8 }}>
              {files.slice(0, 5).map(file => (
                <Tag 
                  key={file.filename}
                  color={file.date === selectedDate.format('YYYY-MM-DD') ? 'blue' : 'default'}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setSelectedDate(dayjs(file.date))}
                >
                  {file.date} ({file.size_mb}MB)
                </Tag>
              ))}
            </Space>
          </div>
        )}
      </Card>

      {/* 日志级别说明 */}
      <Alert
        message="日志级别说明"
        description={
          <Space wrap>
            <Tag color="default">DEBUG - 调试信息</Tag>
            <Tag color="processing">INFO - 一般信息</Tag>
            <Tag color="warning">WARNING - 警告</Tag>
            <Tag color="error">ERROR - 错误</Tag>
            <Tag color="red">CRITICAL - 严重错误</Tag>
          </Space>
        }
        type="info"
        showIcon
        style={{ marginTop: 16 }}
      />
    </div>
  );
};

export default LogsPage;
