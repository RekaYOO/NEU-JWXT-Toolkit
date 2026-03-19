# 项目架构文档 (Architecture)

## 1. 项目结构

```
e:\code\NEUT\
├── docs\                        # 开发文档库
│   ├── API.md                   # 接口文档
│   ├── ARCHITECTURE.md          # 架构文档
│   └── STORAGE.md               # 存储模块文档
│
├── neu_auth\                    # 认证模块（核心）
│   ├── __init__.py              # 导出 NEUAuthClient
│   └── client.py                # CAS登录 + academic挂载点
│
├── neu_academic\                # 成绩模块（扩展）
│   ├── __init__.py              # 导出 AcademicAPI
│   └── api.py                   # 成绩API + 数据模型
│
├── neu_storage\                 # 存储模块（扩展）
│   ├── __init__.py              # 导出 Storage 等
│   ├── storage.py               # 核心存储类
│   └── integration.py           # 与auth/academic集成
│
├── query_grades.py              # 参考实现
└── example_usage.py             # 使用示例
```

---

## 2. 模块关系

```
┌─────────────────────────────────────────────────────────────┐
│                        用户代码层                            │
│  from neu_auth import NEUAuthClient                          │
│  auth = NEUAuthClient(...)                                   │
│  auth.login()                                                │
│  auth.academic.get_scores()                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     neu_auth (核心包)                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ NEUAuthClient                                       │   │
│  │ - _session: requests.Session                        │   │
│  │ - login(): CAS登录                                  │   │
│  │ - get()/post(): HTTP请求                            │   │
│  │ - academic: AcademicAPI (延迟加载)                   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
          │                    │
          │ 延迟导入            │ 延迟导入
          ▼                    ▼
┌─────────────────┐    ┌─────────────────────────────────────┐
│ neu_academic    │    │ neu_storage                         │
│ (扩展包)         │    │ (扩展包)                             │
│ ─────────────── │    │ ─────────────────────────────────── │
│ AcademicAPI     │    │ Storage                             │
│ - 成绩获取      │◄───│ - CSV/JSON/Pickle 存储              │
│ - 绩点计算      │    │ - 配置管理                          │
│                 │    │                                     │
│ CourseScore     │    │ AcademicStorage                     │
│ TermScores      │    │ - 与 AcademicAPI 集成               │
│                 │    │ - 自动保存/加载                     │
│                 │    │                                     │
│                 │    │ AutoLoginManager                    │
│                 │    │ - Session 持久化                    │
│                 │    │ - 自动登录                          │
└─────────────────┘    └─────────────────────────────────────┘
```

---

## 3. 核心设计决策

### 3.1 统一客户端设计

**问题：** 多个Client类造成使用困惑  
**决策：** 通过 `auth.academic` 挂载，统一入口

```python
# 不好的设计
from neu_auth import NEUAuthClient
from neu_academic import AcademicClient

auth = NEUAuthClient(...)
auth.login()
ac = AcademicClient(auth)  # 需要额外创建

# 当前设计
from neu_auth import NEUAuthClient

auth = NEUAuthClient(...)
auth.login()
auth.academic.get_scores()  # 直接访问
```

### 3.2 延迟加载机制

**原因：** 避免循环导入，只在需要时初始化

```python
# NEUAuthClient.__init__
self._academic = None  # 延迟初始化

@property
def academic(self):
    if self._academic is None:
        from neu_academic.api import AcademicAPI
        self._academic = AcademicAPI(self)
    return self._academic
```

### 3.3 使用系统绩点

**问题：** 手动计算可能与系统不一致  
**决策：** 直接使用教务系统返回的 `JD` 字段

```python
# 系统返回的数据
{
    "XSZCJ": "85",    # 成绩
    "JD": "3.5",      # 系统计算的绩点 <-- 使用这个
    "XF": "3.0"       # 学分
}

# 计算公式
GPA = Σ(JD × XF) / Σ(XF)
```

### 3.4 本地数据存储

**问题：** 频繁登录不便，需要离线查看成绩  
**决策：** 独立存储模块，支持 CSV/JSON/Pickle

**设计要点：**
- **独立模块**：`neu_storage` 不强制依赖，按需使用
- **多种格式**：CSV 便于查看，JSON 存储配置，Pickle 存 Session
- **默认目录**：`~/.neu_auth/` 标准位置，自动创建
- **集成类**：`AcademicStorage` 提供一键保存功能

```python
# 基础使用
storage = Storage()
storage.save_scores(scores)  # CSV
storage.save_config(config)   # JSON

# 集成使用（推荐）
academic_storage = AcademicStorage()
academic_storage.fetch_and_save(auth)  # 获取并保存
```

---

## 4. 数据流

### 4.1 登录流程

```
用户调用 auth.login()
    │
    ▼
GET https://pass.neu.edu.cn/tpass/login
    │
    ▼
提取隐藏字段 (lt, execution)
    │
    ▼
RSA加密 (username + password)
    │
    ▼
POST https://pass.neu.edu.cn/tpass/login
    │
    ▼
跟随重定向到 jwxt.neu.edu.cn
    │
    ▼
设置 _logged_in = True
```

### 4.2 成绩获取流程

```
auth.academic.get_scores()
    │
    ├─> 检查 term 参数
    │       │
    │       ├─> 为空: 获取所有学期 → 遍历查询
    │       └─> 指定: 查询单个学期
    │
    ▼
构建请求
    │
    ▼
POST https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do
    │
    ▼
解析响应 JSON
    │
    ▼
提取 rows 数组
    │
    ▼
转换为 List[CourseScore]
    │
    ▼
返回
```

---

## 5. 类设计

### 5.1 NEUAuthClient

**职责：** CAS认证 + HTTP会话管理 + 子模块挂载

```python
class NEUAuthClient:
    # 核心属性
    username: str
    password: str
    _session: requests.Session
    _logged_in: bool
    _academic: AcademicAPI  # 延迟初始化
    
    # 核心方法
    login(target) -> bool
    get(url, **kwargs) -> Response
    post(url, **kwargs) -> Response
    
    # 属性
    @property
    academic -> AcademicAPI
    @property
    is_logged_in -> bool
```

### 5.2 AcademicAPI

**职责：** 成绩数据获取 + 绩点计算

```python
class AcademicAPI:
    # 配置
    TERMS_URL: str
    SCORES_URL: str
    GPA_URL: str
    HEADERS: dict
    
    # 初始化
    _client: NEUAuthClient  # 反向引用
    
    # 方法
    get_terms() -> List[Dict]
    get_scores(term="") -> List[CourseScore]
    get_overall_gpa() -> float
    calculate_gpa(scores) -> float
    get_scores_by_term() -> List[TermScores]
```

### 5.3 数据模型

```python
@dataclass
class CourseScore:
    name: str
    code: str
    score: float
    gpa: float        # 系统返回
    credit: float
    term: str
    course_type: str
    exam_type: str
    is_passed: bool

@dataclass
class TermScores:
    term_code: str
    term_name: str
    courses: List[CourseScore]
    
    @property
    def total_credits -> float
    
    @property
    def gpa -> float  # 使用系统绩点计算
```

---

## 6. 扩展点

<<<<<<< HEAD
### 6.1 组件结构

```
GPACalculator (React Component)
│
├─ 数据状态
│   ├─ courses: 课程列表（真实+模拟）
│   ├─ editingKey: 当前编辑的课程
│   ├─ activeTab: 当前标签页
│   └─ history: 操作历史（用于撤销）
│
├─ 核心功能
│   ├─ 成绩编辑（自动检测真实/模拟状态）
│   ├─ 课程管理（添加/删除/编辑）
│   ├─ 培养计划导入
│   ├─ 数据持久化（导入/导出）
│   └─ 冲突检测（导入时同名课程处理）
│
└─ 服务器存储
    ├─ /api/gpa-simulation/export
    ├─ /api/gpa-simulation/files
    ├─ /api/gpa-simulation/file/{name}
    └─ /api/gpa-simulation/file/{name} (DELETE)
```

### 6.2 课程状态管理

```javascript
// 真实课程（从教务系统导入）
{
  key: 'real_MATH101_0',
  name: '高等数学',
  code: 'MATH101',
  score: 85,
  gpa: 3.5,
  credit: 5.0,
  isReal: true,           // 标记为真实成绩
  isCustom: false,
  originalData: {...},    // 原始数据，用于检测修改
}

// 模拟课程（被修改的真实课程或手动添加）
{
  key: 'imported_123456_0',
  name: '高等数学',
  code: 'MATH101',
  score: 90,              // 修改后的成绩
  gpa: 4.0,
  credit: 5.0,
  isReal: false,          // 标记为模拟
  isCustom: false,
  originalData: null,
}
```

### 6.3 培养计划层级结构

```
AcademicReport (培养计划报告)
├── categories: List[CategoryNode] (顶层类别)
│   ├── [0] 通识类 (A0)
│   │   ├── children: List[CategoryNode]
│   │   │   ├── [0] 数学与自然科学类
│   │   │   │   ├── children: [] (叶节点)
│   │   │   │   └── courses: List[CourseInfo] (实际课程)
│   │   │   │       ├── 线性代数 3.0学分 已通过
│   │   │   │       └── 高等数学①㈠ 5.0学分 未修读
│   │   │   └── [1] 人文与社会科学类
│   │   │       └── ...
│   │   └── courses: [] (中间节点无课程)
│   ├── [1] 学科基础类 (A4)
│   └── [2] 专业方向类 (A5)
└── outside_courses: List[CourseInfo] (方案外课程)
```

**关键特性：**
- 多层嵌套：支持无限层级类别结构
- 叶节点存储：只有叶节点包含实际课程列表
- 路径追踪：每个课程记录完整的类别路径 `category_path: ["通识类", "数学与自然科学类"]`
- 学分计算：递归累加所有子节点课程学分

### 6.4 修改检测逻辑

```javascript
// 当真实课程被修改时，自动变为模拟状态
if (course.isReal && course.originalData) {
  const isModified = 
    course.originalData.score !== newScore ||
    Math.abs(course.originalData.gpa - newGPA) > 0.01 ||
    Math.abs(course.originalData.credit - newCredit) > 0.01;
    
  if (isModified) {
    course.isReal = false;  // 变为模拟课程
  }
}
```

---

## 7. 扩展点

### 7.1 添加新模块
=======
### 6.1 添加新模块
>>>>>>> parent of 7b26e4f8 (1)

参考 `neu_academic` 的模式：

1. 创建新包目录 `neu_xxx/`
2. 实现 `xxx_api.py`，接收 `NEUAuthClient` 作为参数
3. 在 `NEUAuthClient` 添加 property 挂载点

```python
# neu_xxx/api.py
class XxxAPI:
    def __init__(self, auth_client):
        self._client = auth_client

# neu_auth/client.py
@property
def xxx(self):
    if self._xxx is None:
        from neu_xxx.api import XxxAPI
        self._xxx = XxxAPI(self)
    return self._xxx
```

### 6.2 添加新接口

在 `AcademicAPI` 中添加方法，遵循模式：

```python
def new_method(self, param: str) -> ReturnType:
    # 1. 构建请求
    resp = self._client.post(
        self.NEW_URL,
        data={"param": param},
        headers=self.HEADERS
    )
    
    # 2. 解析响应
    data = resp.json()
    if data.get("code") != "0":
        return default_value
    
    # 3. 提取数据
    rows = data["datas"]["xxx"]["rows"]
    
    # 4. 转换模型
    return [Model(r) for r in rows]
```

---

## 7. 依赖关系

```
neu_auth
    ├── requests (HTTP)
    ├── pycryptodome (RSA加密)
    ├── beautifulsoup4 (HTML解析)
    └── lxml (BS4后端)

neu_academic
    └── neu_auth (依赖注入)
```

---

## 8. 编码规范

### 8.1 命名

- 类名: `PascalCase` (e.g., `AcademicAPI`)
- 方法/属性: `snake_case` (e.g., `get_scores`)
- 常量: `UPPER_CASE` (e.g., `TERMS_URL`)
- 私有属性: `_leading_underscore` (e.g., `_client`)

### 8.2 类型注解

所有公共方法必须添加类型注解：

```python
def get_scores(self, term: str = "") -> List[CourseScore]:
    ...
```

### 8.3 文档字符串

类和方法必须包含文档字符串：

```python
def calculate_gpa(self, scores: List[CourseScore]) -> float:
    """
    计算GPA
    
    公式: Σ(绩点×学分)/Σ学分
    
    Args:
        scores: 成绩列表
        
    Returns:
        GPA值
    """
```

---

## 9. 测试策略

### 9.1 单元测试

- 测试数据模型转换
- 测试GPA计算逻辑

### 9.2 集成测试

- 测试完整登录流程
- 测试API调用链

### 9.3 测试账号

```python
TEST_ACCOUNT = {
    "username": "20241643",
    "password": "neu-N0-pwd",
    "expected": {
        "terms": 3,
        "courses": 34,
        "gpa": 3.8243
    }
}
```
