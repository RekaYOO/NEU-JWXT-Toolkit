# 接口文档 (API Documentation)

## 1. 统一入口

所有功能通过 `NEUAuthClient` 实例访问：

```python
from neu_auth import NEUAuthClient

auth = NEUAuthClient("学号", "密码")
auth.login(target="https://jwxt.neu.edu.cn")

# 成绩功能
auth.academic.get_scores()
auth.academic.get_overall_gpa()
```

---

## 2. 认证模块 (neu_auth)

### NEUAuthClient

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | username, password, timeout=15, verify_ssl=True | None | 初始化客户端 |
| `login` | target="https://jwxt.neu.edu.cn" | bool | 执行CAS登录 |
| `get` | url, **kwargs | Response | 发送GET请求 |
| `post` | url, **kwargs | Response | 发送POST请求 |
| `academic` | - | AcademicAPI | 成绩API入口（延迟初始化） |

**内部属性：**
- `_session`: requests.Session 实例
- `_logged_in`: 登录状态标志

---

## 3. 成绩模块 (neu_academic)

### AcademicAPI

挂载点：`NEUAuthClient.academic`

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `get_terms` | - | List[Dict] | 获取有成绩的学期列表 |
| `get_scores` | term="" | List[CourseScore] | 获取成绩，空字符串=全部 |
| `get_overall_gpa` | - | float / None | 系统计算的总绩点 |
| `calculate_gpa` | scores | float | 使用系统绩点计算GPA |
| `get_scores_by_term` | - | List[TermScores] | 按学期分组获取成绩 |

**常量定义：**
```python
TERMS_URL = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcjxnxq.do"
SCORES_URL = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do"
GPA_URL = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/api/wdcj/queryPjxfjd.do"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/*default/index.do",
    "X-Requested-With": "XMLHttpRequest",
}
```

---

## 4. 数据模型

### CourseScore (dataclass)

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `name` | str | KCM | 课程名称 |
| `code` | str | KCH | 课程号 |
| `score` | float | XSZCJ | 成绩（数字） |
| `gpa` | float | JD | **系统返回的单科绩点** |
| `credit` | float | XF | 学分 |
| `term` | str | XNXQDM | 学期代码 |
| `course_type` | str | KCXZDM_DISPLAY | 课程性质（必修/选修） |
| `exam_type` | str | KSLXDM_DISPLAY | 考核方式 |
| `is_passed` | bool | SFJG_DISPLAY | 是否及格 |

### TermScores (dataclass)

| 字段 | 类型 | 说明 |
|------|------|------|
| `term_code` | str | 学期代码 |
| `term_name` | str | 学期名称 |
| `courses` | List[CourseScore] | 课程列表 |
| `total_credits` | float | 总学分（property） |
| `gpa` | float | 学期GPA（property，使用系统绩点计算） |

---

## 5. 外部接口详情

### 5.1 获取学期列表

**Endpoint:** `GET /jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcjxnxq.do`

**Headers:**
- Accept: application/json, text/plain, */*
- Referer: https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/*default/index.do
- X-Requested-With: XMLHttpRequest

**Response:**
```json
{
  "code": "0",
  "datas": {
    "cxwdcjxnxq": {
      "rows": [
        {"XNXQDM": "2025-2026-1", "XNXQMC": "2025-2026学年秋季学期"}
      ]
    }
  }
}
```

### 5.2 获取成绩

**Endpoint:** `POST /jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do`

**Form Data:**
- XNXQDM: 学期代码（空字符串查不到数据，必须指定学期）
- pageSize: 500
- pageNumber: 1

**Response:**
```json
{
  "code": "0",
  "datas": {
    "cxwdcj": {
      "rows": [
        {
          "KCM": "课程名称",
          "KCH": "课程号",
          "XSZCJ": "85",
          "JD": "3.5",
          "XF": "3.0",
          "XNXQDM": "2025-2026-1",
          "KCXZDM_DISPLAY": "必修",
          "KSLXDM_DISPLAY": "考试",
          "SFJG_DISPLAY": "是"
        }
      ]
    }
  }
}
```

### 5.3 获取总绩点

**Endpoint:** `GET /jwapp/sys/cjzhcxapp/api/wdcj/queryPjxfjd.do`

**Response:**
```json
{
  "code": "0",
  "datas": {
    "queryPjxfjd": {
      "ZPJXFJD": "3.8243"
    }
  }
}
```

---

## 6. 存储模块接口 (neu_storage)

### 6.1 Storage 类

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `save_scores` | scores, filename=None | str | 成绩保存为 CSV |
| `load_scores` | filename=None | List[CourseScore] | 从 CSV 加载 |
| `export_scores_by_term` | term_scores, base_filename | List[str] | 按学期导出 |
| `save_config` | config, filename=None | str | 配置保存为 JSON |
| `load_config` | filename=None | dict | 从 JSON 加载 |
| `save_credentials` | username, password, auto_login=False | str | 保存登录凭证 |
| `load_credentials` | - | tuple / None | 加载登录凭证 |
| `save_session` | session_data, filename=None | str | Session 持久化 |
| `load_session` | filename=None | any | 加载 Session |
| `get_storage_info` | - | dict | 获取存储统计 |
| `clear_all` | - | None | 清空所有数据 |

### 6.2 AcademicStorage 类

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `fetch_and_save` | auth, save_by_term=False | dict | 获取并保存成绩 |
| `load_with_meta` | - | dict | 加载成绩及元数据 |
| `compare_with_remote` | auth | dict | 比较本地和远程差异 |

### 6.3 AutoLoginManager 类

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `save_login` | auth, remember_password=False | dict | 保存登录信息 |
| `try_auto_login` | - | NEUAuthClient / None | 尝试自动登录 |
| `clear_login` | - | None | 清除登录信息 |

### 6.4 便捷函数

| 函数 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `quick_save` | auth, data_dir=None | dict | 一键保存所有数据 |

---

## 6. 培养计划模块 (neu_academic/report)

### 6.1 AcademicReportAPI

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `get_report` | - | AcademicReport | 获取完整培养计划 |
| `get_report_from_file` | path | AcademicReport | 从本地JSON文件解析 |
| `export_to_csv` | report, output_dir | Dict[str, str] | 导出为CSV |

### 6.2 数据模型

#### AcademicReport

| 字段 | 类型 | 说明 |
|------|------|------|
| `student_name` | str | 学生姓名 |
| `student_id` | str | 学号 |
| `program_code` | str | 培养方案代码 |
| `total_required` | float | 总要求学分 |
| `calculated_time` | str | 计算时间 |
| `categories` | List[CategoryNode] | 顶层类别列表（通识类/学科基础类等） |
| `outside_courses` | List[CourseInfo] | 方案外课程 |

#### CategoryNode（类别节点）

支持多层嵌套结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `wid` | str | 节点唯一ID |
| `name` | str | 类别名称（如"通识类"、"数学与自然科学类"） |
| `category_code` | str | 类别代码（A0/A4/A5/A6...） |
| `depth` | int | 层级深度（根=0） |
| `required_credits` | float | 要求学分 |
| `path` | List[str] | 从根到本节点的名称路径 |
| `children` | List[CategoryNode] | 子类别列表 |
| `courses` | List[CourseInfo] | 本节点直属课程 |
| `is_leaf` | bool | 是否为叶节点 |

#### CourseInfo（课程信息）

| 字段 | 类型 | 说明 |
|------|------|------|
| `course_code` | str | 课程代码 |
| `course_name` | str | 课程名称 |
| `credit` | float | 学分 |
| `course_nature` | str | 课程性质代码（01=必修, 02=选修） |
| `status` | str | 状态代码（01=已通过, 03=已选课, 04=未修读） |
| `passed` | bool | 是否通过（已通过的唯一判断依据） |
| `score` | str | 成绩显示值 |
| `category_path` | List[str] | 从根到叶的类别路径 |

**课程状态判断：**
- `is_passed`: passed=True → 已通过（计入已修学分）
- `is_selected`: status='03' 且 not passed → 已选课（计入已选学分）
- `is_planned`: status='04' 且 not passed → 未修读（计划中）

### 6.3 层级结构示例

```
通识类 (A0) 要求: 73.0学分
├── 数学与自然科学类 要求: 20.0学分
│   ├── 数学与自然科学类 (叶节点) 要求: 20.0学分
│   │   ├── 线性代数 3.0学分 已通过
│   │   ├── 高等数学①㈠ 5.0学分 未修读
│   │   └── ...
│   └── ...
├── 人文与社会科学类 要求: 18.0学分
│   └── ...
└── ...

学科基础类 (A4) 要求: 45.0学分
├── 学科基础必修课 要求: 35.0学分
│   └── ...
└── 学科基础选修课 要求: 10.0学分
    └── ...
```

### 6.4 后端 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/academic-report` | GET | 获取完整培养计划（支持 `?refresh=true`） |
| `/api/academic-report/refresh` | POST | 强制刷新 |
| `/api/academic-report/summary` | GET | 获取摘要信息 |
| `/api/academic-report/export` | GET | 导出为 CSV |

### 6.5 API 响应格式

#### GET /api/academic-report/summary

学分统计已自动计算：

```json
{
  "credit_summary": {
    "total_required": 160.0,      // 总要求学分
    "total_passed": 85.5,         // 已修学分（已通过课程）
    "total_selected": 15.0,       // 已选学分（在读课程）
    "total_earned": 100.5,        // 已获得学分（已修+已选）
    "completion_rate": 53.4       // 完成百分比
  },
  "category_summary": [
    {
      "name": "通识类",
      "required": 73.0,            // 要求学分
      "passed": 45.0,              // 已修学分
      "selected": 8.0,             // 已选学分
      "earned": 53.0,              // 已获得学分
      "completion_rate": 61.6,     // 完成度百分比
      "course_count": 5,           // 直属课程数
      "total_course_count": 25     // 总课程数（含子类）
    }
  ]
}
```

#### GET /api/academic-report

```json
{
  "student_name": "张三",
  "student_id": "20240001",
  "program_code": "CS2024",
  "total_required": 160.0,
  "calculated_time": "2024-03-18T10:30:00",
  "categories": [
    {
      "wid": "xxx",
      "name": "通识类",
      "category_code": "A0",
      "depth": 0,
      "required_credits": 73.0,
      "path": ["通识类"],
      "is_leaf": false,
      "children": [
        {
          "wid": "yyy",
          "name": "数学与自然科学类",
          "category_code": "A0-1",
          "depth": 1,
          "required_credits": 20.0,
          "path": ["通识类", "数学与自然科学类"],
          "is_leaf": true,
          "children": [],
          "courses": [
            {
              "course_code": "MATH101",
              "course_name": "线性代数",
              "credit": 3.0,
              "passed": true,
              "status": "01",
              "is_passed": true,
              "is_selected": false,
              "is_planned": false,
              "status_display": "已通过",
              "category_path": ["通识类", "数学与自然科学类"]
            }
          ]
        }
      ],
      "courses": []
    }
  ],
  "outside_courses": [],
  "source": "remote",
  "is_fresh": true,
  "last_update": "2024-03-18T10:30:00"
}
```

### 6.6 数据刷新策略

- **优先本地**: 默认加载本地缓存
- **成绩关联**: 成绩更新后自动同步培养计划
- **手动刷新**: 点击刷新按钮强制从云端获取
- **增量更新**: 本地数据与远程对比，显示差异

## 7. 实验选课模块 (neu_academic/experiment)

### 7.1 ExperimentCourseAPI

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `get_semester` | - | str | 获取当前学年学期 |
| `get_courses` | term_code | List[ExperimentCourse] | 获取实验课程列表 |
| `get_rounds` | term, task_id, course_no, project_code | List[ExperimentRound] | 获取实验班列表 |
| `select` | term, task_id, project_code, round_id | dict | 选课 |
| `deselect` | term, task_id, project_code, round_id | dict | 退课 |

### 7.2 数据模型

**ExperimentCourse**: 实验课程
- `task_id`: 任务ID
- `course_name`: 课程名称
- `course_no`: 课程号
- `credit`: 学分
- `experiment_hours`: 实验学时
- `must_do_count`: 必做项目数
- `projects`: 实验项目列表

**ExperimentProject**: 实验项目
- `project_name`: 项目名称
- `project_code`: 项目代码
- `must_do`: 是否必做
- `selected_round_id`: 已选实验班ID
- `select_status`: 选择状态

**ExperimentRound**: 实验班
- `wid`: 轮次ID
- `round_name`: 班级名称
- `teacher`: 教师
- `selected_count`: 已选人数
- `capacity`: 容量
- `week/day/time`: 上课时间
- `conflict`: 是否冲突

### 7.3 后端 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/experiment-courses` | GET | 获取实验课程列表 |
| `/api/experiment-courses/{task_id}/rounds` | GET | 获取实验班列表 |
| `/api/experiment-courses/select` | POST | 选择实验班 |
| `/api/experiment-courses/deselect` | POST | 退选实验班 |

## 8. 用户头像 API (neu_auth)

### 8.1 方法

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `get_user_info` | - | dict | 获取用户信息（含头像URL） |
| `get_avatar` | avatar_token | bytes | 获取头像图片数据 |

### 8.2 后端 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/user/info` | GET | 获取用户信息 |
| `/api/user/avatar` | GET | 获取头像图片（返回二进制） |

## 7. 调用流程

### 7.1 成绩查询流程

```
用户代码
    │
    ▼
auth.academic.get_scores()
    │
    ├─> 调用 get_terms() 获取所有学期
    │       │
    │       ▼
    │   GET /cxwdcjxnxq.do
    │
    ├─> 遍历学期，逐个查询
    │       │
    │       ▼
    │   POST /cxwdcj.do (XNXQDM=学期代码)
    │
    ▼
返回 List[CourseScore]
```

### 7.2 培养计划查询流程

```
用户代码
    │
    ▼
auth.academic_report.get_report()
    │
    ▼
GET /jwapp/sys/pyfagljdapp/modules/pyfazd/pyfazd.do
    │
    ▼
返回 AcademicReport
```

---

## 7. 异常处理

| 异常 | 来源 | 说明 |
|------|------|------|
| `NEULoginError` | neu_auth | 登录失败时抛出 |
| `requests.HTTPError` | requests | HTTP请求错误 |
| `KeyError` | parser | 数据结构解析错误 |

---

## 8. 扩展开发指南

### 添加新接口

在 `AcademicAPI` 类中添加方法：

```python
def new_api_method(self, param: str) -> dict:
    resp = self._client.post(
        self.NEW_URL,
        data={"param": param},
        headers=self.HEADERS
    )
    return resp.json()
```

### 添加新数据模型

```python
from dataclasses import dataclass

@dataclass
class NewModel:
    field1: str
    field2: float
```

---

## 9. 测试数据

测试账号：`20241643`

预期结果：
- 学期数：3
- 课程数：34
- 总绩点：3.8243
