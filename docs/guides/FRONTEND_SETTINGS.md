# 前端设置存储文档

## 概述

前端设置存储工具位于 `frontend/src/utils/settings.js`，用于将用户界面偏好设置保存到浏览器的 `localStorage`，实现设置的持久化。

## 功能特性

- ✅ **自动保存** - 设置变更后自动保存到本地
- ✅ **自动恢复** - 页面启动时自动加载上次设置
- ✅ **智能合并** - 新版本增加列时，保留用户偏好同时显示新列
- ✅ **一键重置** - 提供恢复默认设置功能
- ✅ **通用接口** - 统一的 API 方便添加新的设置项

## 使用方法

### 1. 导入设置工具

```javascript
import { columnSettings, saveSetting, loadSetting } from '../utils/settings';
```

### 2. 列设置（已实现）

成绩页面的列显示设置已自动支持本地保存：

```javascript
// 初始化时加载保存的列配置
const [columnConfig, setColumnConfig] = useState(() => 
  columnSettings.load(getDefaultColumns())
);

// 切换列时自动保存
const toggleColumn = (key) => {
  setColumnConfig(prev => {
    const newConfig = prev.map(col => 
      col.key === key ? { ...col, visible: !col.visible } : col
    );
    columnSettings.save(newConfig);  // 自动保存
    return newConfig;
  });
};

// 重置为默认
const resetColumnConfig = () => {
  const defaultConfig = getDefaultColumns();
  setColumnConfig(defaultConfig);
  columnSettings.reset();  // 清除本地保存
};
```

### 3. 添加新的设置项

参考以下模式添加其他设置：

```javascript
// 1. 在 SettingKeys 中添加键名
export const SettingKeys = {
  COLUMN_CONFIG: 'columnConfig',
  PAGE_SIZE: 'pageSize',        // 新增：分页大小
  THEME: 'theme',               // 新增：主题
  // ...
};

// 2. 创建专门的工具函数（可选）
export const pageSizeSettings = {
  KEY: SettingKeys.PAGE_SIZE,
  
  save: (size) => saveSetting(pageSizeSettings.KEY, size),
  
  load: (defaultSize = 10) => 
    loadSetting(pageSizeSettings.KEY, defaultSize),
  
  reset: () => removeSetting(pageSizeSettings.KEY)
};

// 3. 在组件中使用
const [pageSize, setPageSize] = useState(() => pageSizeSettings.load(10));

const handlePageSizeChange = (size) => {
  setPageSize(size);
  pageSizeSettings.save(size);
};
```

## API 参考

### 基础函数

| 函数 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `saveSetting(key, value)` | key: string, value: any | void | 保存设置项 |
| `loadSetting(key, defaultValue)` | key: string, default: any | any | 加载设置项 |
| `removeSetting(key)` | key: string | void | 删除设置项 |
| `clearAllSettings()` | - | void | 清空所有设置 |

### 列设置专用

| 函数 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `columnSettings.save(columns)` | columns: Array | void | 保存列配置 |
| `columnSettings.load(defaultColumns)` | defaultColumns: Array | Array | 加载并合并列配置 |
| `columnSettings.reset()` | - | void | 恢复默认列配置 |

## 存储格式

设置项存储在浏览器的 `localStorage` 中，键名格式为：

```
neu_toolbox:<key>
```

例如：
- `neu_toolbox:columnConfig` - 列配置
- `neu_toolbox:pageSize` - 分页大小

## 注意事项

1. **存储限制** - localStorage 通常有 5MB 限制，不要存储大量数据
2. **隐私安全** - 不要存储敏感信息（密码等）
3. **版本兼容** - 列设置会自动合并，但如果数据结构大变可能需要重置
4. **隐私模式** - 部分浏览器的隐私/无痕模式下 localStorage 不可用

## 未来扩展建议

以下设置项可以考虑添加本地保存：

- [ ] **分页大小** - 表格每页显示条数
- [ ] **主题设置** - 亮色/暗色模式
- [ ] **默认筛选** - 成绩页面默认只显示某学期
- [ ] **排序偏好** - 默认按成绩/学期排序
- [ ] **语言设置** - 中英文切换
