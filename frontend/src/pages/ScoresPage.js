import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { 
  Table, Card, Statistic, Row, Col, Button, Tag, message, Alert,
  Tooltip, Dropdown, Checkbox, Space, InputNumber, Modal
} from 'antd';
import { 
  ReloadOutlined, TrophyOutlined, BookOutlined, SafetyOutlined,
  SettingOutlined, CloudSyncOutlined, DatabaseOutlined,
  CheckCircleOutlined, CalculatorOutlined, ExclamationCircleOutlined
} from '@ant-design/icons';
import GPACalculator from '../components/GPACalculator';
import { getScores, refreshScores, getAcademicReport, refreshAcademicReport, cancelRequest } from '../services/api';
import { columnSettings } from '../utils/settings';
import dayjs from 'dayjs';
import './ScoresPage.css';

const DEFAULT_COLUMNS = [
  { key: 'name', title: '课程名称', visible: true, width: 200 },
  { key: 'code', title: '课程代码', visible: true, width: 120 },
  { key: 'score', title: '成绩', visible: true, width: 80 },
  { key: 'gpa', title: '绩点', visible: true, width: 80 },
  { key: 'credit', title: '学分', visible: true, width: 80 },
  { key: 'term_display', title: '学期', visible: true, width: 180 },
  { key: 'course_type', title: '课程性质', visible: true, width: 100 },
  { key: 'course_category', title: '课程类别', visible: false, width: 150 },
  { key: 'general_category', title: '通识类别', visible: false, width: 150 },
  { key: 'exam_type', title: '考核方式', visible: false, width: 100 },
  { key: 'exam_status', title: '考试状态', visible: false, width: 100 },
  { key: 'is_passed', title: '状态', visible: true, width: 80 },
];

const getDefaultColumns = () => JSON.parse(JSON.stringify(DEFAULT_COLUMNS));

// 比对两组成绩数据是否相同
const isScoresEqual = (localScores, remoteScores) => {
  if (!localScores || !remoteScores) return false;
  if (localScores.length !== remoteScores.length) return false;
  
  const localMap = new Map(localScores.map(s => [`${s.code}-${s.term}`, s]));
  
  for (const remote of remoteScores) {
    const key = `${remote.code}-${remote.term}`;
    const local = localMap.get(key);
    if (!local) return false;
    if (local.score !== remote.score || local.gpa !== remote.gpa) return false;
  }
  return true;
};

const ScoresPage = () => {
  const [allScores, setAllScores] = useState([]);
  const [displayScores, setDisplayScores] = useState([]);
  const [dataLoaded, setDataLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [dataInfo, setDataInfo] = useState({ source: 'local', is_fresh: false, last_update: null });
  
  // 更新提示
  const [updateModalVisible, setUpdateModalVisible] = useState(false);
  const [pendingUpdateData, setPendingUpdateData] = useState(null);
  
  // 列配置
  const [columnConfig, setColumnConfig] = useState(() => 
    columnSettings.load(getDefaultColumns(), 'columnConfig')
  );
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  
  // 分页
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (total) => `共 ${total} 门课程`,
  });

  // GPA计算器
  const [isSimulating, setIsSimulating] = useState(false);
  const gpaCalculatorRef = useRef(null);

  // ===== 加载策略：优先从后端data目录读取 =====
  useEffect(() => {
    loadData();
    
    // 组件卸载时取消未完成的请求
    return () => {
      cancelRequest('scores');
    };
  }, []);

  // 加载数据（优先后端本地data目录）
  const loadData = async () => {
    try {
      // 调用后端API，优先读取本地data目录
      const data = await getScores(false);
      
      const scoresWithId = data.scores.map((s, index) => ({
        ...s,
        _id: `${s.code}-${s.term}-${index}`
      }));
      
      setAllScores(scoresWithId);
      setDisplayScores(scoresWithId);
      setDataInfo({
        source: data.source,
        is_fresh: data.is_fresh,
        last_update: data.last_update,
      });
      setDataLoaded(true);
      
      // 如果本地数据不是最新的，异步检查更新
      if (data.source === 'local' && !data.is_fresh) {
        setTimeout(() => checkForUpdates(scoresWithId), 1000);
      }
    } catch (error) {
      // 如果是请求取消，不显示错误
      if (error.name === 'CanceledError' || error.name === 'AbortError') {
        console.log('[Scores] 请求已取消');
        return;
      }
      message.error('获取成绩失败: ' + error.message);
      setDataLoaded(true);
    }
  };

  // 检查云端更新
  const checkForUpdates = async (currentScores) => {
    try {
      // 获取云端数据
      const remoteData = await getScores(true);
      
      if (remoteData.source === 'remote') {
        // 比对数据是否相同
        const isSame = isScoresEqual(currentScores, remoteData.scores);
        
        if (isSame) {
          // 数据相同，静默更新时间戳，不弹窗提示
          setDataInfo(prev => ({
            ...prev,
            is_fresh: true,
            last_update: remoteData.last_update
          }));
          // 不显示提示，避免干扰
        } else {
          // 数据不同，弹窗提示
          setPendingUpdateData(remoteData);
          setUpdateModalVisible(true);
        }
      }
    } catch (e) {
      console.log('检查更新失败:', e);
    }
  };

  // 获取并更新远程数据
  const fetchAndUpdateRemoteData = async () => {
    try {
      // 同时刷新成绩和培养计划
      await Promise.all([
        refreshScores(),
        refreshAcademicReport()
      ]);
      
      const data = await getScores(true);
      const scoresWithId = data.scores.map((s, index) => ({
        ...s,
        _id: `${s.code}-${s.term}-${index}`
      }));
      
      setAllScores(scoresWithId);
      setDisplayScores(scoresWithId);
      setDataInfo({
        source: 'remote',
        is_fresh: true,
        last_update: data.last_update,
      });
      
      return true;
    } catch (e) {
      message.error('获取数据失败: ' + e.message);
      return false;
    }
  };

  // 确认更新
  const handleConfirmUpdate = async () => {
    setUpdateModalVisible(false);
    message.loading('正在更新数据...', 0);
    
    const success = await fetchAndUpdateRemoteData();
    
    message.destroy();
    if (success) {
      message.success('成绩和培养计划已更新');
    }
    setPendingUpdateData(null);
  };

  // 取消更新
  const handleCancelUpdate = () => {
    setUpdateModalVisible(false);
    setPendingUpdateData(null);
  };

  // 手动刷新
  const handleRefresh = async () => {
    setRefreshing(true);
    message.loading('正在刷新数据...', 0);
    
    try {
      const success = await fetchAndUpdateRemoteData();
      
      message.destroy();
      if (success) {
        message.success('数据已刷新');
      }
    } catch (error) {
      message.destroy();
      message.error('刷新失败: ' + error.message);
    } finally {
      setRefreshing(false);
    }
  };

  // 列配置
  const toggleColumn = (key) => {
    setColumnConfig(prev => {
      const newConfig = prev.map(col => 
        col.key === key ? { ...col, visible: !col.visible } : col
      );
      columnSettings.save(newConfig, 'columnConfig');
      return newConfig;
    });
  };
  
  const resetColumnConfig = () => {
    const defaultConfig = getDefaultColumns();
    setColumnConfig(defaultConfig);
    columnSettings.reset('columnConfig');
    message.success('已恢复默认列设置');
  };

  // 筛选选项
  const getFilterOptions = (key) => {
    const values = [...new Set(allScores.map(s => s[key]).filter(Boolean))];
    return values.map(v => ({ text: v, value: v }));
  };

  // 表格变化处理
  const handleTableChange = (newPagination, newFilters, newSorter) => {
    setPagination({
      ...pagination,
      current: newPagination.current,
      pageSize: newPagination.pageSize,
    });

    let filtered = [...allScores];
    Object.keys(newFilters).forEach(key => {
      if (newFilters[key] && newFilters[key].length > 0) {
        filtered = filtered.filter(item => newFilters[key].includes(item[key]));
      }
    });

    if (newSorter && newSorter.field && newSorter.order) {
      const { field, order } = newSorter;
      filtered.sort((a, b) => {
        let aVal, bVal;
        if (field === 'score') {
          aVal = a.score_value || parseFloat(a.score) || 0;
          bVal = b.score_value || parseFloat(b.score) || 0;
        } else {
          aVal = a[field];
          bVal = b[field];
        }
        
        if (field === 'score' || field === 'gpa' || field === 'credit') {
          aVal = parseFloat(aVal) || 0;
          bVal = parseFloat(bVal) || 0;
          return order === 'ascend' ? aVal - bVal : bVal - aVal;
        }
        
        aVal = String(aVal || '');
        bVal = String(bVal || '');
        const cmp = aVal.localeCompare(bVal, 'zh-CN');
        return order === 'ascend' ? cmp : -cmp;
      });
    }

    setDisplayScores(filtered);
  };

  // 表格列
  const tableColumns = useMemo(() => {
    return columnConfig
      .filter(col => col.visible)
      .map(col => {
        const column = {
          title: <div className="column-header">{col.title}</div>,
          dataIndex: col.key,
          key: col.key,
          width: col.width,
          sorter: true,
        };

        if (col.key !== 'score' && col.key !== 'gpa' && col.key !== 'credit') {
          column.filters = getFilterOptions(col.key);
          column.filterSearch = true;
          column.onFilter = (value, record) => record[col.key] === value;
        }

        if (col.key === 'score' || col.key === 'gpa' || col.key === 'credit') {
          column.filterDropdown = ({ setSelectedKeys, selectedKeys, confirm, clearFilters }) => (
            <div style={{ padding: 8 }}>
              <Space direction="vertical">
                <InputNumber placeholder="最小值" value={selectedKeys?.[0]} onChange={(v) => setSelectedKeys([v, selectedKeys?.[1]])} style={{ width: 120 }} />
                <InputNumber placeholder="最大值" value={selectedKeys?.[1]} onChange={(v) => setSelectedKeys([selectedKeys?.[0], v])} style={{ width: 120 }} />
                <Space>
                  <Button type="primary" size="small" onClick={confirm}>确定</Button>
                  <Button size="small" onClick={clearFilters}>重置</Button>
                </Space>
              </Space>
            </div>
          );
          column.onFilter = (value, record) => {
            if (!value || value.length < 2) return true;
            const [min, max] = value;
            const minVal = parseFloat(min) || 0;
            const maxVal = parseFloat(max) || 999;
            const recordVal = col.key === 'score' 
              ? (parseFloat(record.score_value) || 0)
              : (parseFloat(record[col.key]) || 0);
            return recordVal >= minVal && recordVal <= maxVal;
          };
        }

        if (col.key === 'score') {
          column.render = (score) => {
            let color = 'green';
            if (score < 60) color = 'red';
            else if (score < 70) color = 'orange';
            else if (score < 80) color = 'blue';
            return <Tag color={color}>{score}</Tag>;
          };
        }

        if (col.key === 'gpa') {
          column.render = (gpa) => <span className="gpa">{gpa?.toFixed(2)}</span>;
        }

        if (col.key === 'is_passed') {
          column.render = (passed) => (
            <Tag color={passed ? 'success' : 'error'}>{passed ? '通过' : '未通过'}</Tag>
          );
          column.filters = [{ text: '通过', value: true }, { text: '未通过', value: false }];
        }

        return column;
      });
  }, [columnConfig, allScores]);

  // 列选择菜单
  const columnMenuItems = [
    ...columnConfig.map(col => ({
      key: col.key,
      label: (
        <Checkbox checked={col.visible} onChange={() => toggleColumn(col.key)}>
          {col.title}
        </Checkbox>
      ),
    })),
    { type: 'divider' },
    {
      key: 'reset',
      label: (
        <Button type="link" size="small" onClick={resetColumnConfig} style={{ padding: 0 }}>
          恢复默认
        </Button>
      ),
    },
  ];

  // 刷新按钮
  const refreshButtonText = useMemo(() => {
    const lastUpdate = dataInfo.last_update ? dayjs(dataInfo.last_update) : null;
    if (dataInfo.source === 'remote' || dataInfo.is_fresh) return '已是最新';
    if (lastUpdate) return `本地数据 · ${lastUpdate.format('MM-DD')}`;
    return '刷新';
  }, [dataInfo]);

  const refreshButtonIcon = useMemo(() => {
    if (dataInfo.source === 'remote' || dataInfo.is_fresh) return <CheckCircleOutlined />;
    return <ReloadOutlined />;
  }, [dataInfo]);

  // 统计
  const stats = useMemo(() => {
    if (!allScores.length) return { totalCourses: 0, avgGpa: 0, passedCount: 0, failedCount: 0, totalCredits: 0 };
    
    const totalCredits = allScores.reduce((sum, s) => sum + (s.credit || 0), 0);
    const weightedGpa = totalCredits > 0 
      ? allScores.reduce((sum, s) => sum + (s.gpa || 0) * (s.credit || 0), 0) / totalCredits 
      : 0;
    
    return {
      totalCourses: allScores.length,
      avgGpa: weightedGpa,
      passedCount: allScores.filter(s => s.is_passed).length,
      failedCount: allScores.filter(s => !s.is_passed).length,
      totalCredits: totalCredits,
    };
  }, [allScores]);

  // GPA模拟
  const startSimulation = () => {
    setIsSimulating(true);
    if (gpaCalculatorRef.current) {
      gpaCalculatorRef.current.startSimulation();
    }
  };
  const exitSimulation = () => {
    setIsSimulating(false);
    if (gpaCalculatorRef.current) {
      gpaCalculatorRef.current.stopSimulation();
    }
  };
  const handleSimulatingChange = (simulating) => {
    setIsSimulating(simulating);
  };

  // 数据未加载完成时显示空状态（而不是loading）
  if (!dataLoaded) {
    return (
      <div className="scores-page">
        <Row gutter={16} className="stats-row">
          <Col xs={24} sm={12} md={6}><Card><Statistic title="课程总数" value="--" /></Card></Col>
          <Col xs={24} sm={12} md={6}><Card><Statistic title="平均绩点" value="--" /></Card></Col>
          <Col xs={24} sm={12} md={6}><Card><Statistic title="已通过" value="--" /></Card></Col>
          <Col xs={24} sm={12} md={6}><Card><Statistic title="总学分" value="--" /></Card></Col>
        </Row>
        <Card className="scores-table-card" title="成绩明细">
          <div style={{ textAlign: 'center', padding: '40px', color: '#999' }}>
            正在加载本地数据...
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="scores-page">
      {/* 统计卡片 */}
      <Row gutter={16} className="stats-row">
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic title="课程总数" value={stats.totalCourses} prefix={<BookOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic title="平均绩点" value={stats.avgGpa} precision={3} prefix={<TrophyOutlined />} valueStyle={{ color: '#3f8600' }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic title="已通过" value={stats.passedCount} prefix={<SafetyOutlined />} valueStyle={{ color: '#3f8600' }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic title="总学分" value={stats.totalCredits} precision={1} />
          </Card>
        </Col>
      </Row>

      {/* GPA模拟计算器 */}
      <GPACalculator
        ref={gpaCalculatorRef}
        realScores={allScores}
        onSimulatingChange={handleSimulatingChange}
      />

      {/* 成绩表格 */}
      {!isSimulating && (
        <Card
          className="scores-table-card"
          title={
            <Space>
              <span>成绩明细</span>
              <Dropdown
                menu={{ items: columnMenuItems }}
                open={columnMenuOpen}
                onOpenChange={setColumnMenuOpen}
                placement="bottomLeft"
                arrow
              >
                <Button icon={<SettingOutlined />} size="small">列设置</Button>
              </Dropdown>
            </Space>
          }
          extra={
            <Space>
              <Tooltip title="进入GPA模拟模式：编辑成绩、预估GPA、导入培养计划课程">
                <Button type="primary" icon={<CalculatorOutlined />} onClick={startSimulation}>
                  GPA模拟
                </Button>
              </Tooltip>
              <Tooltip title={dataInfo.last_update ? `最后更新: ${dayjs(dataInfo.last_update).format('YYYY-MM-DD HH:mm')}` : '点击刷新云端数据'}>
                <Button
                  type={dataInfo.source === 'remote' || dataInfo.is_fresh ? 'default' : 'primary'}
                  icon={refreshButtonIcon}
                  loading={refreshing}
                  onClick={handleRefresh}
                >
                  {refreshButtonText}
                </Button>
              </Tooltip>
            </Space>
          }
        >
          <Table
            columns={tableColumns}
            dataSource={displayScores}
            rowKey="_id"
            scroll={{ x: 'max-content' }}
            pagination={pagination}
            onChange={handleTableChange}
            bordered
            size="middle"
          />
        </Card>
      )}

      {/* 更新提示弹窗 */}
      <Modal
        title={
          <Space>
            <ExclamationCircleOutlined style={{ color: '#faad14' }} />
            <span>发现新成绩</span>
          </Space>
        }
        open={updateModalVisible}
        onOk={handleConfirmUpdate}
        onCancel={handleCancelUpdate}
        okText="立即更新"
        cancelText="稍后更新"
      >
        <Alert
          message="检测到云端有新成绩数据"
          description='云端成绩与本地不同，点击"立即更新"同步最新数据（包括成绩和培养计划）。'
          type="info"
          showIcon
        />
        {pendingUpdateData && (
          <div style={{ marginTop: 16 }}>
            <p>本地课程: {allScores.length} 门</p>
            <p>云端课程: {pendingUpdateData.scores?.length} 门</p>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default ScoresPage;
