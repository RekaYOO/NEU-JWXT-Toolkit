# 本地数据存储文档 (Storage)

## 1. 概述

`neu_storage` 模块提供本地数据持久化功能，支持：
- 成绩数据 CSV 存储
- 登录配置 JSON 存储
- Session 持久化
- 自动目录管理

## 2. 存储位置

默认存储目录：`~/.neu_auth/` (用户主目录下)

```
~/.neu_auth/
├── scores.csv                  # 成绩总表
├── scores_2024-2025-1.csv      # 按学期分表
├── scores_2024-2025-2.csv
├── scores_2025-2026-1.csv
├── config.json                 # 配置文件
├── credentials.json            # 登录凭证（可选）
├── session.pkl                 # Session 持久化
└── last_fetch_meta.json        # 上次获取元数据
```

## 3. 核心类

### 3.1 Storage

基础存储类，提供文件操作。

```python
from neu_storage import Storage, StorageConfig

# 使用默认配置
storage = Storage()

# 自定义数据目录
config = StorageConfig(data_dir="D:/neu_data")
storage = Storage(config)
```

**方法：**

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `save_scores` | scores, filename=None | str | 保存成绩到 CSV |
| `load_scores` | filename=None | List[CourseScore] | 从 CSV 加载成绩 |
| `export_scores_by_term` | term_scores, base_filename | List[str] | 按学期导出 |
| `save_config` | config, filename=None | str | 保存配置到 JSON |
| `load_config` | filename=None | dict | 从 JSON 加载配置 |
| `save_credentials` | username, password, auto_login=False | str | 保存登录凭证 |
| `load_credentials` | - | tuple / None | 加载登录凭证 |
| `save_session` | session_data, filename=None | str | 保存 Session |
| `load_session` | filename=None | any | 加载 Session |
| `list_files` | - | List[str] | 列出所有文件 |
| `get_storage_info` | - | dict | 获取存储统计 |
| `clear_all` | - | None | 清空所有数据 |

### 3.2 AcademicStorage

与 AcademicAPI 集成的高级存储类。

```python
from neu_storage import AcademicStorage

academic_storage = AcademicStorage()

# 获取并保存
result = academic_storage.fetch_and_save(auth, save_by_term=True)
# 返回: {
#   "total_courses": 34,
#   "overall_gpa": 3.8243,
#   "files": [...]
# }

# 加载成绩及元数据
data = academic_storage.load_with_meta()
# 返回: {"scores": [...], "meta": {...}}

# 比较本地和远程差异
diff = academic_storage.compare_with_remote(auth)
```

### 3.3 AcademicReportStorage

培养计划存储类（关联成绩更新，无自动过期）。

```python
from neu_storage import AcademicReportStorage

report_storage = AcademicReportStorage()

# 智能获取（优先本地，成绩更新后自动刷新）
result = report_storage.get_report_smart(auth, force_refresh=False)
# 返回: {
#   "report": {...},
#   "source": "local" | "remote",
#   "last_update": datetime,
#   "is_fresh": bool
# }

# 强制刷新
result = report_storage.refresh_report(auth)

# 加载本地数据
local_data = report_storage.load_report()
```

**存储策略：**
- 优先加载本地缓存（无过期时间）
- 仅在以下情况刷新：
  - 本地无数据
  - `force_refresh=True`（手动刷新）
  - 成绩文件更新时间 > 培养计划更新时间

### 3.4 AutoLoginManager

自动登录管理器。

```python
from neu_storage import AutoLoginManager

auto_login = AutoLoginManager()

# 保存登录信息
auto_login.save_login(auth, remember_password=True)

# 尝试自动登录
auth = auto_login.try_auto_login()
if auth:
    print("自动登录成功")
else:
    print("需要手动登录")

# 清除登录信息
auto_login.clear_login()
```

### 3.5 quick_save

一键保存所有数据。

```python
from neu_storage import quick_save

# 保存成绩、session 等
result = quick_save(auth)
# 返回完整保存结果
```

## 4. 存储格式

### 4.1 CSV 格式（成绩）

#### scores.csv

```csv
course_code,course_name,score,gpa,credit,term,course_type,exam_type,is_passed,saved_at
MATH101,高等数学,85.0,3.5,5.0,2024-2025-1,必修,考试,是,2026-03-16T22:09:36
...
```

**字段说明：**
- `course_code`: 课程代码
- `course_name`: 课程名称
- `score`: 成绩
- `gpa`: 绩点（系统返回）
- `credit`: 学分
- `term`: 学期
- `course_type`: 课程性质
- `exam_type`: 考核方式
- `is_passed`: 是否及格（是/否）
- `saved_at`: 保存时间

### 4.2 JSON 格式（培养计划）

#### academic_report.json

```json
{
  "report": {
    "student_name": "张三",
    "student_id": "20240001",
    "program_code": "CS2024",
    "total_required": 160.0,
    "calculated_time": "2024-01-15T10:30:00",
    "categories": [
      {
        "wid": "course_group_wid_xxx",
        "name": "通识类",
        "category_code": "A0",
        "depth": 0,
        "required_credits": 73.0,
        "path": ["通识类"],
        "is_leaf": false,
        "children": [
          {
            "wid": "course_group_wid_yyy",
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
                "course_nature": "01",
                "status": "01",
                "passed": true,
                "score": "96",
                "is_passed": true,
                "is_selected": false,
                "is_planned": false,
                "status_display": "已通过",
                "category_path": ["通识类", "数学与自然科学类"],
                "node_wid": "course_group_wid_yyy"
              }
            ]
          }
        ],
        "courses": []
      }
    ],
    "outside_courses": []
  },
  "username": "20240001",
  "saved_at": "2024-01-15T10:30:00"
}
```

<<<<<<< HEAD
**层级结构说明：**
- `categories` 是顶层类别列表（通识类、学科基础类、专业方向类、实践类）
- 每个类别可以有 `children` 子类别（多级嵌套）
- 叶节点（`is_leaf=true`）包含实际的 `courses` 课程列表
- `path` 字段记录从根到当前节点的完整路径
- `category_path` 记录课程所属的路径

### 4.3 JSON 格式（GPA模拟）

#### gpa_simulations/GPA模拟_YYYY-MM-DD.json

```json
{
  "version": "1.0",
  "exportTime": "2024-01-15T10:30:00",
  "stats": {
    "totalCourses": 34,
    "totalCredits": 85.5,
    "weightedGPA": 3.75,
    "passedCount": 32,
    "realCount": 20,
    "customCount": 14
  },
  "courses": [
    {
      "name": "高等数学",
      "code": "MATH101",
      "credit": 5.0,
      "score": "90",
      "gpa": 4.0,
      "term": "2024-2025-1",
      "courseType": "必修",
      "isReal": false,
      "isCustom": false,
      "originalData": null
    }
  ]
}
```

**字段说明：**
- `version`: 文件格式版本
- `exportTime`: 导出时间
- `stats`: 统计数据
  - `totalCourses`: 总课程数
  - `totalCredits`: 总学分
  - `weightedGPA`: 加权GPA
  - `realCount`: 真实课程数
  - `customCount`: 自定义/模拟课程数
- `courses`: 课程列表，与成绩数据结构相同

=======
>>>>>>> parent of 7b26e4f8 (1)
## 5. 使用示例

### 基础使用

```python
from neu_auth import NEUAuthClient
from neu_storage import Storage

auth = NEUAuthClient("学号", "密码")
auth.login()

# 获取成绩
scores = auth.academic.get_scores()

# 保存到 CSV
storage = Storage()
filepath = storage.save_scores(scores)
print(f"已保存: {filepath}")

# 从 CSV 加载
loaded = storage.load_scores()
print(f"加载了 {len(loaded)} 门课程")
```

### 自动保存

```python
from neu_auth import NEUAuthClient
from neu_storage import AcademicStorage

auth = NEUAuthClient("学号", "密码")
auth.login()

# 自动获取并保存
academic_storage = AcademicStorage()
result = academic_storage.fetch_and_save(auth, save_by_term=True)

print(f"保存了 {result['total_courses']} 门课程")
print(f"总绩点: {result['overall_gpa']}")
for f in result['files']:
    print(f"  - {f}")
```

### 配置持久化

```python
from neu_storage import Storage

storage = Storage()

# 保存配置
config = {
    "default_term": "2025-2026-1",
    "auto_refresh": True
}
storage.save_config(config, "app_config.json")

# 加载配置
loaded = storage.load_config("app_config.json")
print(loaded["default_term"])
```

### 自动登录

```python
from neu_storage import AutoLoginManager

auto_login = AutoLoginManager()

# 尝试自动登录
auth = auto_login.try_auto_login()

if auth:
    print("自动登录成功")
    # 刷新 session
    auto_login.save_login(auth)
else:
    print("Session 过期，需要手动登录")
    # 手动登录后保存
    auth = NEUAuthClient("学号", "密码")
    auth.login()
    auto_login.save_login(auth, remember_password=True)
```

## 6. 集成到 NEUAuthClient（可选）

如需直接在 NEUAuthClient 上挂载 storage：

```python
# neu_auth/client.py 中添加

@property
def storage(self):
    if self._storage is None:
        from neu_storage import Storage
        self._storage = Storage()
    return self._storage
```

然后使用：

```python
auth = NEUAuthClient("学号", "密码")
auth.login()

# 直接通过 auth.storage 访问
auth.storage.save_scores(auth.academic.get_scores())
```

## 7. 安全说明

⚠️ **注意：**

1. **密码明文存储**：`save_credentials` 使用明文存储密码，仅建议本地开发使用
2. **Session 文件**：`session.pkl` 包含敏感信息，不要上传到版本控制
3. **数据目录**：默认存储在用户主目录，多用户系统注意权限

**建议：**
- 生产环境使用环境变量或密钥管理服务
- 将 `~/.neu_auth/` 添加到 `.gitignore`
- 定期清理旧的 session 文件

## 8. 扩展开发

### 添加新的存储格式

```python
class Storage:
    def save_scores_excel(self, scores, filename="scores.xlsx"):
        """保存为 Excel 格式"""
        import pandas as pd
        df = pd.DataFrame([...])
        df.to_excel(self._get_path(filename))
```

### 添加数据加密

```python
import hashlib

def save_credentials_encrypted(self, username, password):
    """加密存储凭证"""
    encrypted = encrypt(password)  # 使用密钥加密
    self.save_config({
        "username": username,
        "password": encrypted
    }, "credentials.json")
```
