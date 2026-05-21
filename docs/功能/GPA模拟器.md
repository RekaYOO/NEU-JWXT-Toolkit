# GPA模拟器文档

## 概述

GPA模拟器是一个交互式工具，允许学生模拟不同成绩对总GPA的影响。支持导入真实成绩进行修改、添加虚拟课程、保存/加载模拟方案等功能。

## 功能特性

- ✅ **真实成绩导入** - 从教务系统自动导入已有成绩
- ✅ **成绩模拟修改** - 修改已有课程成绩，查看GPA变化
- ✅ **虚拟课程添加** - 添加未修课程进行GPA预估
- ✅ **培养计划导入** - 从培养计划导入课程进行模拟
- ✅ **数据持久化** - 保存/加载GPA模拟方案
- ✅ **冲突检测** - 导入时检测同名课程并提供选择
- ✅ **历史撤销** - 支持撤销操作
- ✅ **智能分类** - 自动识别真实/模拟课程

## 使用方法

### 启动GPA模拟器

在成绩页面点击 "GPA模拟" 按钮启动模拟器。

### 基本操作

| 操作 | 说明 |
|------|------|
| 修改成绩 | 点击成绩列输入框，输入新成绩后按回车或失焦 |
| 修改绩点 | 点击绩点列输入框进行修改 |
| 修改学分 | 点击学分列输入框进行修改 |
| 添加课程 | 点击"添加课程"按钮添加空白课程 |
| 删除课程 | 点击删除按钮移除课程 |
| 撤销操作 | 点击"撤销"按钮撤销最近操作 |

### 课程类型

| 类型 | 标签 | 说明 |
|------|------|------|
| 真实 | 🟢 真实 | 从教务系统导入且未修改的课程 |
| 模拟 | ⚪ 模拟 | 被修改过的真实课程 |
| 自定义 | 🟠 自定义 | 手动添加的新课程 |
| 计划 | 🔵 计划 | 从培养计划导入的课程 |

### 数据导入

#### 从培养计划导入

培养计划支持多层嵌套结构（如：通识类 → 数学与自然科学类 → 具体课程）。

1. 点击 "从培养计划导入" 按钮
2. 系统自动加载培养计划（包含层级结构）
3. 在弹出的抽屉中选择要导入的课程
4. 点击"导入"按钮

**层级结构示例：**
```
通识类 (73学分)
├── 数学与自然科学类 (20学分)
│   ├── 线性代数 3学分 已通过
│   ├── 高等数学①㈠ 5学分 未修读
│   └── ...
├── 人文与社会科学类 (18学分)
│   └── ...
└── ...

学科基础类 (45学分)
├── 学科基础必修课 (35学分)
└── 学科基础选修课 (10学分)
```

**课程状态：**
- 已通过：已修读并获得学分
- 已选课：已选课但未完成
- 未修读：计划中但未选课

#### 从文件导入

1. 点击 "从文件导入" 按钮
2. 在弹出的文件列表中选择保存的模拟方案
3. 如有同名课程冲突，选择保留导入的或现有的

### 数据保存

1. 点击 "保存" 按钮
2. 在弹出的对话框中输入文件名（默认：GPA模拟_YYYY-MM-DD）
3. 点击确认保存到服务器

## 技术实现

### 组件架构

```
GPACalculator (React ForwardRef Component)
│
├─ State Management
│   ├─ courses: Course[]
│   ├─ editingKey: string | null
│   ├─ activeTab: 'all' | 'real' | 'custom' | 'passed' | 'pending'
│   ├─ history: Course[][]
│   ├─ historyIndex: number
│   └─ hasUnsavedChanges: boolean
│
├─ Refs
│   └─ editingValuesRef: { [key: string]: any }
│
├─ Handlers
│   ├─ handleScoreChange()
│   ├─ handleGPAChange()
│   ├─ handleCreditChange()
│   ├─ addCustomCourse()
│   ├─ deleteCourse()
│   └─ saveEdit()
│
└─ Server API
    ├─ exportGPASimulation()
    ├─ listGPASimulationFiles()
    ├─ getGPASimulationFile()
    └─ deleteGPASimulationFile()
```

### 课程数据结构

```typescript
interface Course {
  key: string;           // 唯一标识
  name: string;          // 课程名称
  code: string;          // 课程代码
  credit: number;        // 学分
  score: string | number; // 成绩
  gpa: number;           // 绩点
  term: string;          // 学期
  courseType: string;    // 课程类型
  isReal: boolean;       // 是否真实成绩
  isCustom: boolean;     // 是否自定义添加
  fromPlan?: boolean;    // 是否来自培养计划
  originalData?: any;    // 原始数据（用于检测修改）
}
```

### 修改检测逻辑

```javascript
// 当真实课程被修改时，自动变为模拟状态
const handleScoreChange = (key, newScore) => {
  const newCourses = courses.map(c => {
    if (c.key !== key) return c;
    
    // 检查是否修改了真实课程
    const isModified = c.isReal && c.originalData && 
                       c.originalData.score !== newScore;
    
    return { 
      ...c, 
      score: newScore,
      isReal: isModified ? false : c.isReal 
    };
  });
  
  setCourses(newCourses);
  saveToHistory(newCourses);
};
```

### 冲突检测（导入时）

```javascript
// 检查同名课程数据是否不同
const detectConflicts = (imported, existing) => {
  const conflicts = [];
  
  imported.forEach(imp => {
    existing.forEach(exist => {
      if (imp.name === exist.name) {
        const isDifferent = 
          Math.abs((imp.gpa || 0) - (exist.gpa || 0)) > 0.01 ||
          imp.score !== exist.score ||
          Math.abs((imp.credit || 0) - (exist.credit || 0)) > 0.01;
        
        if (isDifferent) {
          conflicts.push({
            imported: imp,
            existing: exist,
            choice: 'imported' // 默认选择导入的
          });
        }
      }
    });
  });
  
  return conflicts;
};
```

## API 接口

### 后端 API

```bash
# 导出GPA模拟
POST /api/gpa-simulation/export
{
  "filename": "GPA模拟_2024-01-15.json",
  "data": {
    "version": "1.0",
    "exportTime": "...",
    "stats": {...},
    "courses": [...]
  }
}

# 获取文件列表
GET /api/gpa-simulation/files

# 读取文件
GET /api/gpa-simulation/file/{filename}

# 删除文件
DELETE /api/gpa-simulation/file/{filename}
```

### 前端 API 服务

```javascript
import { 
  exportGPASimulation,
  listGPASimulationFiles,
  getGPASimulationFile,
  deleteGPASimulationFile 
} from '../services/api';

// 导出
await exportGPASimulation('my_simulation.json', data);

// 列出文件
const files = await listGPASimulationFiles();

// 读取文件
const data = await getGPASimulationFile('my_simulation.json');

// 删除文件
await deleteGPASimulationFile('my_simulation.json');
```

## 使用场景

### 场景1：预估重修后的GPA

1. 启动GPA模拟器（自动导入现有成绩）
2. 找到需要重修的课程
3. 修改该课程的成绩和绩点
4. 查看总GPA变化
5. 保存模拟方案

### 场景2：规划未来学期课程

1. 启动GPA模拟器
2. 从培养计划导入未来要修的课程
3. 预估每门课程的成绩
4. 查看预估GPA
5. 调整选课计划以达到目标GPA

### 场景3：对比不同成绩方案

1. 启动GPA模拟器
2. 保存当前状态为方案A
3. 修改成绩
4. 保存为方案B
5. 在两个方案间切换对比

## 注意事项

1. **数据安全** - GPA模拟文件保存在服务器本地，不要存储敏感信息
2. **文件命名** - 建议使用有意义的文件名，如 "方案A_重修高数.json"
3. **版本兼容** - 未来版本可能会更新数据结构，旧文件可能需要转换
4. **GPA计算** - 使用5分制加权平均：Σ(绩点×学分)/Σ学分
5. **真实课程保护** - 修改真实课程后变为模拟状态，原数据保留在originalData中

## 常见问题

**Q: 修改成绩后为什么课程变成"模拟"类型？**
A: 为了区分原始成绩和修改后的成绩，修改真实课程后会自动标记为模拟类型。

**Q: 如何恢复原始成绩？**
A: 点击撤销按钮或重新从教务系统刷新数据。

**Q: GPA模拟文件保存在哪里？**
A: 保存在 `data/gpa_simulations/` 目录下，可以导出到其他设备。

**Q: 可以同时保存多个模拟方案吗？**
A: 可以，每个方案保存为不同的文件，可以随时切换加载。

## 未来扩展

- [ ] 支持更多GPA计算规则（4分制、百分制等）
- [ ] 成绩趋势图表
- [ ] 目标GPA计算器（计算需要多少分才能达到目标GPA）
- [ ] 分享模拟方案给其他用户
- [ ] 导出为Excel/PDF报告
