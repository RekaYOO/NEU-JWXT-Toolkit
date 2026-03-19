# 前端开发规范

本文档规范前端页面开发，确保功能一致性。

## 1. 页面结构模板

所有数据展示页面必须以 `ScoresPage.js` 为模板：

```javascript
import React, { useState, useEffect, useMemo } from 'react';
import { Table, Card, Statistic, Row, Col, Button, Tag, message, Spin, Tooltip, Dropdown, Checkbox, Space, InputNumber } from 'antd';
import { ReloadOutlined, SettingOutlined } from '@ant-design/icons';
import { columnSettings } from '../utils/settings';
import dayjs from 'dayjs';
import './PageName.css';

// 1. 默认列配置（关键列默认显示）
const DEFAULT_COLUMNS = [
  { key: 'field1', title: '字段1', visible: true, width: 120 },
  { key: 'field2', title: '字段2', visible: true, width: 100 },
  { key: 'field3', title: '字段3', visible: false, width: 150 }, // 次要列默认隐藏
];

const getDefaultColumns = () => JSON.parse(JSON.stringify(DEFAULT_COLUMNS));

const PageName = () => {
  // 2. 数据状态
  const [allData, setAllData] = useState([]);
  const [displayData, setDisplayData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  
  // 3. 列配置（使用独立 key）
  const [columnConfig, setColumnConfig] = useState(() => 
    columnSettings.load(getDefaultColumns(), 'pageNameColumnConfig')
  );
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  
  // 4. 分页状态
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 20,
    showSizeChanger: true,
    pageSizeOptions: ['10', '20', '50', '100'],
    showTotal: (total) => `共 ${total} 条`,
  });

  // 5. 加载数据
  const loadData = async (forceRefresh = false) => { ... };

  // 6. 刷新数据
  const handleRefresh = async () => { ... };

  // 7. 列设置
  const toggleColumn = (key) => { ... };
  const resetColumnConfig = () => { ... };

  // 8. 表格变化处理
  const handleTableChange = (newPagination, newFilters, newSorter) => { ... };

  // 9. 构建表格列
  const tableColumns = useMemo(() => { ... }, [columnConfig, allData]);

  // 10. 列选择菜单
  const columnMenuItems = [ ... ];

  // 11. 刷新按钮文本
  const refreshButtonText = useMemo(() => { ... }, [dataInfo]);
};
```

## 2. 表格列规范

### 2.1 所有列必须同时支持筛选和排序

```javascript
const column = {
  title: col.title,
  dataIndex: col.key,
  key: col.key,
  width: col.width,
  sorter: true,  // 所有列支持排序
};

// 非数字列：文本筛选
if (col.key !== 'score' && col.key !== 'gpa' && col.key !== 'credit') {
  column.filters = getFilterOptions(col.key);
  column.filterSearch = true;
  column.onFilter = (value, record) => record[col.key] === value;
}

// 数字列：范围筛选（必须实现）
if (col.key === 'score' || col.key === 'gpa' || col.key === 'credit') {
  column.filterDropdown = ({ setSelectedKeys, selectedKeys, confirm, clearFilters }) => (
    <div style={{ padding: 8 }}>
      <Space direction="vertical">
        <InputNumber
          placeholder="最小值"
          value={selectedKeys?.[0]}
          onChange={(v) => setSelectedKeys([v, selectedKeys?.[1]])}
          style={{ width: 120 }}
        />
        <InputNumber
          placeholder="最大值"
          value={selectedKeys?.[1]}
          onChange={(v) => setSelectedKeys([selectedKeys?.[0], v])}
          style={{ width: 120 }}
        />
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
    const recordVal = parseFloat(record[col.key]) || 0;
    return recordVal >= min && recordVal <= max;
  };
}
```

### 2.2 handleTableChange 实现

```javascript
const handleTableChange = (newPagination, newFilters, newSorter) => {
  // 更新分页
  setPagination({
    ...pagination,
    current: newPagination.current,
    pageSize: newPagination.pageSize,
  });

  // 处理筛选
  let filtered = [...allData];
  Object.keys(newFilters).forEach(key => {
    if (newFilters[key] && newFilters[key].length > 0) {
      filtered = filtered.filter(item => newFilters[key].includes(item[key]));
    }
  });

  // 处理排序
  if (newSorter && newSorter.field && newSorter.order) {
    const { field, order } = newSorter;
    filtered.sort((a, b) => {
      let aVal = a[field];
      let bVal = b[field];
      
      // 数字排序
      if (field === 'score' || field === 'gpa' || field === 'credit') {
        aVal = parseFloat(aVal) || 0;
        bVal = parseFloat(bVal) || 0;
        return order === 'ascend' ? aVal - bVal : bVal - aVal;
      }
      
      // 字符串排序
      aVal = String(aVal || '');
      bVal = String(bVal || '');
      const cmp = aVal.localeCompare(bVal, 'zh-CN');
      return order === 'ascend' ? cmp : -cmp;
    });
  }

  setDisplayData(filtered);
};
```

## 3. 列设置功能

### 3.1 列选择菜单

```javascript
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
```

### 3.2 切换列显示

```javascript
const toggleColumn = (key) => {
  setColumnConfig(prev => {
    const newConfig = prev.map(col => 
      col.key === key ? { ...col, visible: !col.visible } : col
    );
    columnSettings.save(newConfig, 'pageNameColumnConfig');  // 独立 key
    return newConfig;
  });
};

const resetColumnConfig = () => {
  const defaultConfig = getDefaultColumns();
  setColumnConfig(defaultConfig);
  columnSettings.reset('pageNameColumnConfig');
  message.success('已恢复默认列设置');
};
```

## 4. 刷新按钮规范

```javascript
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

const refreshButtonIcon = useMemo(() => {
  if (dataInfo.source === 'remote' || dataInfo.is_fresh) {
    return <CheckCircleOutlined />;
  }
  return <ReloadOutlined />;
}, [dataInfo]);
```

## 5. 响应式设计

### 5.1 统计卡片

```css
/* 默认：4列 */
.stats-row { margin-bottom: 16px; }
.stats-row .ant-card { text-align: center; }

/* 平板：2列 */
@media (max-width: 768px) {
  .stats-row .ant-statistic-content { font-size: 16px; }
  .stats-row .ant-statistic-title { font-size: 12px; }
}

/* 手机：2列 */
@media (max-width: 576px) {
  .stats-row .ant-statistic-content { font-size: 14px; }
}
```

### 5.2 表格

```javascript
<Table
  columns={tableColumns}
  dataSource={displayData}
  rowKey="_id"
  scroll={{ x: 'max-content' }}  // 必须：支持横向滚动
  pagination={pagination}
  onChange={handleTableChange}
  bordered
  size="middle"
/>
```

## 6. 新增页面检查清单

- [ ] 创建 `PageName.js` 和 `PageName.css`
- [ ] 定义 `DEFAULT_COLUMNS`，关键列 `visible: true`
- [ ] 使用 `columnSettings.load/save/reset` 并传入独立 key
- [ ] 所有列支持排序 (`sorter: true`)
- [ ] 所有列支持筛选（文本或数值范围）
- [ ] 实现列设置下拉菜单（Dropdown + Checkbox）
- [ ] 刷新按钮显示本地保存时间
- [ ] 后端 API 返回 `last_update` (ISO 格式)
- [ ] 响应式设计（统计卡片、表格）
- [ ] 添加到路由配置

## 7. LocalStorage Key 命名

| 页面 | Key |
|------|-----|
| 成绩 | `columnConfig` |
| 培养计划 | `academicReportColumnConfig` |
| 新课程 | `xxxColumnConfig` |

## 8. 参考实现

- **标准参考**: `frontend/src/pages/ScoresPage.js`
- **列设置工具**: `frontend/src/utils/settings.js`
- **样式参考**: `frontend/src/pages/ScoresPage.css`
