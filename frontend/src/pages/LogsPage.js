import React, { useState, useEffect, useCallback } from 'react';
import { 
  Card, Table, Select, DatePicker, Button, Tag, Space, 
  Statistic, Row, Col, Alert, Input, message, Spin,
  Radio, Typography, Empty, Grid, Descriptions, Modal
} from 'antd';
import { 
  FileTextOutlined, DownloadOutlined, DeleteOutlined,
  SearchOutlined, ReloadOutlined, InfoCircleOutlined,
  WarningOutlined, CloseCircleOutlined, CheckCircleOutlined
} from '@ant-design/icons';
import { getLogSummary, getLogFiles, getLogContent, tailLog, searchLogs, cleanupLogs } from '../services/api';
import dayjs from 'dayjs';
import {
  MobileDetailDrawer,
  MobileFilterButton,
  MobileFilterChips,
  MobileFilterDrawer,
} from '../components/mobile/MobileUX';
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
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  const [mobileDetail, setMobileDetail] = useState(null);
  const [mobileDraft, setMobileDraft] = useState(null);
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;

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
  const handleSearch = async (
    keyword = searchKeyword,
    category = selectedCategory,
  ) => {
    if (!keyword.trim()) {
      message.warning('请输入搜索关键词');
      return;
    }
    
    setSearchLoading(true);
    try {
      const data = await searchLogs(keyword, category, 7, 100);
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

  const confirmCleanup = () => {
    Modal.confirm({
      title: '清理旧日志？',
      content: '将删除 30 天以前的日志文件，此操作无法撤销。',
      okText: '确认清理',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: handleCleanup,
    });
  };

  const openMobileFilters = () => {
    setMobileDraft({
      category: selectedCategory,
      date: selectedDate,
      level: selectedLevel,
      keyword: searchKeyword,
      view: viewMode === 'tail' ? 'tail' : 'content',
    });
    setMobileFilterOpen(true);
  };

  const applyMobileFilters = () => {
    if (!mobileDraft) return;
    setSelectedCategory(mobileDraft.category);
    setSelectedDate(mobileDraft.date);
    setSelectedLevel(mobileDraft.level);
    setSearchKeyword(mobileDraft.keyword);
    setMobileFilterOpen(false);
    if (mobileDraft.keyword.trim()) {
      handleSearch(mobileDraft.keyword, mobileDraft.category);
    } else {
      setViewMode(mobileDraft.view);
    }
  };

  const resetMobileFilters = () => {
    const reset = {
      category: 'system',
      date: dayjs(),
      level: null,
      keyword: '',
      view: 'content',
    };
    setMobileDraft(reset);
    setSelectedCategory(reset.category);
    setSelectedDate(reset.date);
    setSelectedLevel(reset.level);
    setSearchKeyword('');
    setViewMode('content');
  };

  const mobileActiveFilterCount = [
    selectedCategory !== 'system',
    !selectedDate?.isSame(dayjs(), 'day'),
    Boolean(selectedLevel),
    Boolean(searchKeyword),
    viewMode === 'tail',
  ].filter(Boolean).length;
  const mobileFilterChips = [
    selectedCategory !== 'system' && {
      key: 'category',
      label: `分类：${categoryOptions.find(item => item.value === selectedCategory)?.label}`,
    },
    !selectedDate?.isSame(dayjs(), 'day') && {
      key: 'date',
      label: `日期：${selectedDate?.format('YYYY-MM-DD')}`,
    },
    selectedLevel && { key: 'level', label: `级别：${selectedLevel}` },
    searchKeyword && { key: 'keyword', label: `关键词：${searchKeyword}` },
    viewMode === 'tail' && { key: 'view', label: '最新 100 行' },
  ].filter(Boolean);

  const clearMobileFilter = (key) => {
    if (key === 'category') {
      setSelectedCategory('system');
      if (viewMode === 'search' && searchKeyword) {
        handleSearch(searchKeyword, 'system');
      }
    }
    if (key === 'date') setSelectedDate(dayjs());
    if (key === 'level') setSelectedLevel(null);
    if (key === 'keyword') {
      setSearchKeyword('');
      setViewMode('content');
    }
    if (key === 'view') setViewMode('content');
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
              onClick={confirmCleanup}
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
        {isMobile && (
          <>
            <div className="logs-mobile-tools">
              <MobileFilterButton
                activeCount={mobileActiveFilterCount}
                onClick={openMobileFilters}
              >
                查看与筛选
              </MobileFilterButton>
              <Button
                icon={<ReloadOutlined />}
                onClick={() => {
                  loadLogContent();
                  loadSummary();
                }}
              >
                刷新
              </Button>
            </div>
            <MobileFilterChips items={mobileFilterChips} onClear={clearMobileFilter} />
          </>
        )}
        {!isMobile && <Space wrap style={{ marginBottom: 16 }}>
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
        </Space>}

        {/* 搜索栏 */}
        {!isMobile && <Space style={{ marginBottom: 16, display: 'flex' }}>
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
        </Space>}

        {/* 日志内容表格 */}
        {isMobile ? (
          <Spin spinning={loading}>
            <div className="mobile-log-list">
              {entries.map((entry, index) => (
                <button
                  type="button"
                  className="mobile-log-card"
                  key={`${entry.timestamp}-${index}`}
                  onClick={() => setMobileDetail(entry)}
                >
                  <span className="mobile-log-card__head">
                    <Tag color={levelColors[entry.level?.toUpperCase()] || 'default'}>
                      {entry.level?.toUpperCase() || 'UNKNOWN'}
                    </Tag>
                    <time>{entry.timestamp || '-'}</time>
                  </span>
                  <strong>{entry.logger || '未知日志器'}</strong>
                  <code>{entry.message || '-'}</code>
                </button>
              ))}
              {!entries.length && !loading && <Empty description="暂无日志记录" />}
            </div>
          </Spin>
        ) : (
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
        )}

        {/* 日志文件列表 */}
        {files.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Text type="secondary">最近日志文件：</Text>
            <Space wrap size="small" className="recent-log-files" style={{ marginTop: 8 }}>
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
      <MobileFilterDrawer
        open={mobileFilterOpen}
        onClose={() => setMobileFilterOpen(false)}
        onApply={applyMobileFilters}
        onReset={resetMobileFilters}
        title="日志查看与筛选"
      >
        {mobileDraft && (
          <>
            <label className="mobile-field-label">日志分类</label>
            <Select
              value={mobileDraft.category}
              options={categoryOptions.map(option => ({
                label: option.label, value: option.value,
              }))}
              onChange={category => setMobileDraft(current => ({ ...current, category }))}
            />
            <label className="mobile-field-label">日期</label>
            <DatePicker
              value={mobileDraft.date}
              onChange={date => setMobileDraft(current => ({ ...current, date }))}
            />
            <label className="mobile-field-label">日志级别</label>
            <Select
              allowClear
              value={mobileDraft.level}
              options={['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map(value => ({
                label: value, value,
              }))}
              onChange={level => setMobileDraft(current => ({ ...current, level }))}
            />
            <label className="mobile-field-label">查看范围</label>
            <Radio.Group
              value={mobileDraft.view}
              onChange={event => setMobileDraft(current => ({
                ...current, view: event.target.value,
              }))}
              optionType="button"
              buttonStyle="solid"
            >
              <Radio.Button value="content">全部内容</Radio.Button>
              <Radio.Button value="tail">最新100行</Radio.Button>
            </Radio.Group>
            <label className="mobile-field-label">关键词</label>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              value={mobileDraft.keyword}
              onChange={event => setMobileDraft(current => ({
                ...current, keyword: event.target.value,
              }))}
              placeholder="留空则按日期查看"
            />
          </>
        )}
      </MobileFilterDrawer>
      <MobileDetailDrawer
        open={Boolean(mobileDetail)}
        onClose={() => setMobileDetail(null)}
        title="日志详情"
      >
        {mobileDetail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="时间">{mobileDetail.timestamp || '-'}</Descriptions.Item>
            <Descriptions.Item label="级别">{mobileDetail.level || 'UNKNOWN'}</Descriptions.Item>
            <Descriptions.Item label="日志器">{mobileDetail.logger || '-'}</Descriptions.Item>
            <Descriptions.Item label="消息">
              <pre className="mobile-log-detail">{mobileDetail.message || '-'}</pre>
            </Descriptions.Item>
          </Descriptions>
        )}
      </MobileDetailDrawer>
    </div>
  );
};

export default LogsPage;
