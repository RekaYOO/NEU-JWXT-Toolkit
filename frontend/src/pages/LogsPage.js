import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { 
  Card, Table, Select, DatePicker, Button, Tag, Space, 
  Statistic, Row, Col, Alert, Input, message, Spin,
  Radio, Typography, Empty, Grid, Descriptions, Modal, Drawer
} from 'antd';
import { 
  FileTextOutlined, DownloadOutlined, DeleteOutlined,
  SearchOutlined, ReloadOutlined
} from '@ant-design/icons';
import { getLogSummary, getLogFiles, getLogContent, tailLog, searchLogs, cleanupLogs } from '../services/api';
import dayjs from 'dayjs';
import {
  MobileFilterButton,
  MobileFilterChips,
  MobileFilterDrawer,
} from '../components/mobile/MobileUX';
import './LogsPage.css';

const { Option } = Select;
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

const detailLabels = {
  event: '事件标识',
  outcome: '结果',
  request_id: '请求 ID',
  error_id: '错误编号',
  method: '请求方法',
  path: '请求路径',
  status_code: '状态码',
  response_time_ms: '耗时（ms）',
  response_size_bytes: '响应大小（字节）',
  client_ip: '客户端 IP',
  peer_ip: '连接节点 IP',
  scheme: '协议',
  gateway_state: '访问网关状态',
  access_session_id: '访问会话 ID',
  user_id: 'NEU 账号',
  session_user: '会话账号',
  user_agent: 'User-Agent',
  subject: '账号主体',
  reason: '原因',
  auth_method: '认证方式',
  network_mode: '网络模式',
  remember: '记住登录',
  trust_device: '信任设备',
  clear_data: '清理数据',
  component: '组件',
  error_type: '异常类型',
  error_code: '错误代码',
  trace: '安全调用位置',
};

const formatDetailValue = (value) => {
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
};

const outcomeLabels = {
  success: '成功', failure: '失败', blocked: '已拦截', denied: '已拒绝',
  error: '异常', pending: '进行中',
};

const reasonLabels = {
  wrong_password: '密码错误',
  webvpn_required: '需要使用 WebVPN',
  rate_limit: '失败次数过多',
  gateway_not_configured: '访问密码未配置',
  login_rejected: '登录被拒绝',
  direct_access_failed: '校园网直连失败',
  request_error: '认证请求失败',
  flow_missing: '认证流程不存在',
  flow_replaced: '认证流程已被替换',
};

const eventTitles = {
  http_access: 'HTTP 请求',
  application_error: '应用异常',
  access_gateway_login: '网站访问验证',
  access_gateway_logout: '退出网站访问',
  neu_login: 'NEU 账号登录',
  neu_logout: 'NEU 账号退出',
  neu_session_restore: 'NEU 会话恢复',
  webvpn_qr_login: 'WebVPN 二维码登录',
  webvpn_password_login: 'WebVPN 密码登录',
  webvpn_sms_send: 'WebVPN 短信发送',
  webvpn_sms_verify: 'WebVPN 短信验证',
  tracking_recovery_login: '成绩追踪登录恢复',
};

const compactText = (value, limit = 180) => {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
};

const parseStructuredMessage = (message) => {
  if (!message) return null;
  const start = message.indexOf('{');
  if (start < 0) return null;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < message.length; index += 1) {
    const character = message[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
      continue;
    }
    if (character === '"') quoted = true;
    else if (character === '{') depth += 1;
    else if (character === '}') {
      depth -= 1;
      if (depth === 0) {
        try {
          const parsed = JSON.parse(message.slice(start, index + 1));
          return parsed && typeof parsed === 'object' ? parsed : null;
        } catch {
          return null;
        }
      }
    }
  }
  return null;
};

// 兼容尚未重启的旧后端：旧接口只有 message，也能按真实日志格式生成摘要。
const normalizeLogEntry = (entry = {}) => {
  if (entry.summary) return entry;
  const payload = parseStructuredMessage(entry.message);
  const event = payload?.event;
  if (event === 'http_access') {
    const duration = payload.response_time_ms === undefined ? '' : ` · ${payload.response_time_ms} ms`;
    return {
      ...entry,
      event_type: entry.event_type || event,
      event_title: entry.event_title || eventTitles[event],
      summary: `${payload.method || 'HTTP'} ${payload.path || '-'} · ${payload.status_code ?? '-'}${duration}`,
      details: entry.details || payload,
    };
  }
  if (event && eventTitles[event]) {
    const outcome = outcomeLabels[payload.outcome] || '状态未知';
    const reason = payload.reason ? ` · ${reasonLabels[payload.reason] || payload.reason}` : '';
    const method = payload.auth_method && !payload.reason ? ` · ${payload.auth_method}` : '';
    return {
      ...entry,
      event_type: entry.event_type || event,
      event_title: entry.event_title || eventTitles[event],
      summary: `${outcome}${reason}${method}`,
      details: entry.details || payload,
    };
  }
  return {
    ...entry,
    event_type: entry.event_type || 'generic_system',
    event_title: entry.event_title || '普通日志',
    summary: compactText(entry.message) || '空消息',
    details: entry.details || {},
  };
};

const orderNewestFirst = (entries) => {
  const normalized = entries.map(normalizeLogEntry);
  const withTimestamp = normalized.filter(entry => entry.timestamp);
  if (withTimestamp.length > 1) {
    const first = withTimestamp[0].timestamp;
    const last = withTimestamp[withTimestamp.length - 1].timestamp;
    if (first < last) return [...normalized].reverse();
  }
  return [...normalized].sort((left, right) => (
    String(right.timestamp || '').localeCompare(String(left.timestamp || ''))
  ));
};

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
  const [selectedEventType, setSelectedEventType] = useState(null);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [viewMode, setViewMode] = useState('content'); // content / tail / search
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  const [detailEntry, setDetailEntry] = useState(null);
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
      
      setEntries(orderNewestFirst(data.entries || []));
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
      const formatted = (data.results || []).map(r => normalizeLogEntry({
        timestamp: r.timestamp || `${r.date} 00:00:00`,
        level: r.level || 'INFO',
        logger: r.logger || `${r.category}`,
        message: r.message,
        event_type: r.event_type || 'generic_system',
        event_title: r.event_title || '普通日志',
        summary: r.summary || r.message,
        details: r.details || {},
        structured: Boolean(r.structured),
      }));
      setEntries(orderNewestFirst(formatted));
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
      eventType: selectedEventType,
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
    setSelectedEventType(mobileDraft.eventType);
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
      eventType: null,
      view: 'content',
    };
    setMobileDraft(reset);
    setSelectedCategory(reset.category);
    setSelectedDate(reset.date);
    setSelectedLevel(reset.level);
    setSearchKeyword('');
    setSelectedEventType(null);
    setViewMode('content');
  };

  const mobileActiveFilterCount = [
    selectedCategory !== 'system',
    !selectedDate?.isSame(dayjs(), 'day'),
    Boolean(selectedLevel),
    Boolean(selectedEventType),
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
    selectedEventType && {
      key: 'eventType',
      label: `类型：${entries.find(item => item.event_type === selectedEventType)?.event_title || selectedEventType}`,
    },
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
    if (key === 'eventType') setSelectedEventType(null);
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

  const eventTypeOptions = useMemo(() => {
    const options = new Map();
    entries.forEach(entry => {
      const value = entry.event_type || 'generic_system';
      if (!options.has(value)) {
        options.set(value, entry.event_title || '普通日志');
      }
    });
    return Array.from(options, ([value, label]) => ({ value, label }));
  }, [entries]);

  const displayedEntries = useMemo(() => (
    selectedEventType
      ? entries.filter(entry => entry.event_type === selectedEventType)
      : entries
  ), [entries, selectedEventType]);

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
      title: '类型',
      dataIndex: 'event_title',
      key: 'event_title',
      width: 190,
      render: (text) => <span className="log-event-title">{text || '普通日志'}</span>,
    },
    {
      title: '摘要',
      dataIndex: 'summary',
      key: 'summary',
      ellipsis: true,
      render: (text) => <span className="log-summary">{text || '-'}</span>,
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

          <Select
            value={selectedEventType}
            onChange={setSelectedEventType}
            options={eventTypeOptions}
            style={{ width: 190 }}
            placeholder="消息类型"
            allowClear
          />

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
              {displayedEntries.map((entry, index) => (
                <button
                  type="button"
                  className="mobile-log-card"
                  key={`${entry.timestamp}-${index}`}
                  onClick={() => setDetailEntry(entry)}
                >
                  <span className="mobile-log-card__head">
                    <Tag color={levelColors[entry.level?.toUpperCase()] || 'default'}>
                      {entry.level?.toUpperCase() || 'UNKNOWN'}
                    </Tag>
                    <time>{entry.timestamp || '-'}</time>
                  </span>
                  <strong>{entry.event_title || '普通日志'}</strong>
                  <span className="mobile-log-card__summary">{entry.summary || '-'}</span>
                </button>
              ))}
              {!displayedEntries.length && !loading && <Empty description="暂无符合条件的日志" />}
            </div>
          </Spin>
        ) : (
          <Table
            columns={columns}
            dataSource={displayedEntries}
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
              emptyText: <Empty description="暂无符合条件的日志" />,
            }}
            onRow={(record) => ({
              className: 'log-table-row',
              tabIndex: 0,
              onClick: () => setDetailEntry(record),
              onKeyDown: event => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  setDetailEntry(record);
                }
              },
            })}
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
            <label className="mobile-field-label">消息类型</label>
            <Select
              allowClear
              value={mobileDraft.eventType}
              options={eventTypeOptions}
              onChange={eventType => setMobileDraft(current => ({ ...current, eventType }))}
              placeholder="全部消息类型"
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
      <Drawer
        open={Boolean(detailEntry)}
        onClose={() => setDetailEntry(null)}
        title={detailEntry?.event_title || '日志详情'}
        placement={isMobile ? 'bottom' : 'right'}
        width={isMobile ? undefined : 680}
        height={isMobile ? '88dvh' : undefined}
        destroyOnClose
      >
        {detailEntry && (
          <Descriptions column={1} bordered size="small" className="log-detail-descriptions">
            <Descriptions.Item label="摘要">{detailEntry.summary || '-'}</Descriptions.Item>
            <Descriptions.Item label="时间">{detailEntry.timestamp || '-'}</Descriptions.Item>
            <Descriptions.Item label="级别">
              <Tag color={levelColors[detailEntry.level?.toUpperCase()] || 'default'}>
                {detailEntry.level?.toUpperCase() || 'UNKNOWN'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="日志器">{detailEntry.logger || '-'}</Descriptions.Item>
            {Object.entries(detailEntry.details || {}).map(([key, value]) => (
              <Descriptions.Item key={key} label={detailLabels[key] || key}>
                <pre className="log-detail-value">{formatDetailValue(value)}</pre>
              </Descriptions.Item>
            ))}
            <Descriptions.Item label="原始消息">
              <pre className="log-detail-value">{detailEntry.message || '-'}</pre>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
};

export default LogsPage;
