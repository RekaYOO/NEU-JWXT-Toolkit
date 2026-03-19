# 日志系统文档

## 概述

`neu_log` 模块提供完整的日志记录和管理功能，支持分级分类存储、自动轮转、API 查看等功能。

## 功能特性

- ✅ **分级日志** - DEBUG/INFO/WARNING/ERROR/CRITICAL
- ✅ **分类存储** - system/access/error/login/sync
- ✅ **自动轮转** - 按天分割，自动清理旧日志
- ✅ **访问日志** - 自动记录所有 API 请求
- ✅ **日志查看** - 通过 Web 界面查看、搜索、下载

## 项目结构

```
neu_log/
├── __init__.py          # 模块导出
├── logger.py            # 核心日志功能
├── access_logger.py     # 访问日志中间件
└── manager.py           # 日志管理器
```

## 日志存储

日志文件存储在 `data/logs/` 目录下：

```
data/logs/
├── system_2024-03-17.log   # 系统日志
├── access_2024-03-17.log   # 访问日志
├── error_2024-03-17.log    # 错误日志
├── login_2024-03-17.log    # 登录日志
└── sync_2024-03-17.log     # 同步日志
```

## 使用方法

### 1. 基础日志记录

```python
from neu_log import get_logger, LogCategory

# 获取系统日志记录器
logger = get_logger("my_module", LogCategory.SYSTEM)

# 记录日志
logger.debug("调试信息")
logger.info("一般信息")
logger.warning("警告信息")
logger.error("错误信息")
```

### 2. 结构化日志

```python
from neu_log import StructuredLogger, LogCategory

logger = StructuredLogger("my_module", LogCategory.SYNC)

# 添加上下文
logger.with_context(user_id="20220001", action="sync_scores")

# 记录带上下文的日志
logger.info("开始同步成绩")
logger.error("同步失败", error_code=500)
```

### 3. 访问日志中间件

FastAPI 已自动集成：

```python
from neu_log import LogConfig, FastAPILogMiddleware

# 添加中间件
app.add_middleware(FastAPILogMiddleware, config=LogConfig())
```

所有 API 请求会自动记录：
```
2024-03-17 21:30:00 [INFO] neu.access.access: API GET /api/scores 200 45.23ms
```

### 4. 日志管理器

```python
from neu_log import LogManager, LogCategory

manager = LogManager()

# 获取日志文件列表
files = manager.get_log_files(category=LogCategory.SYSTEM, days=7)

# 读取日志内容
entries = manager.read_log(
    LogCategory.SYSTEM,
    "2024-03-17",
    level="ERROR",
    search="timeout",
    limit=100
)

# 查看日志末尾
entries = manager.tail_log(LogCategory.ACCESS, "2024-03-17", lines=50)

# 搜索日志
results = manager.search_logs("error", LogCategory.ERROR, days=7)

# 清理旧日志
deleted = manager.clear_old_logs(keep_days=30)
```

## 日志分类说明

| 分类 | 用途 | 示例 |
|------|------|------|
| `system` | 系统运行日志 | 服务启动、配置加载 |
| `access` | API 访问日志 | HTTP 请求记录 |
| `error` | 错误日志 | 异常堆栈 |
| `login` | 登录相关 | 登录成功/失败 |
| `sync` | 数据同步 | 成绩同步记录 |

## Web 界面

日志查看页面：`http://localhost:3000/logs`

功能：
- 按分类/日期/级别筛选日志
- 实时查看最新日志（tail）
- 关键词搜索
- 下载日志文件
- 清理旧日志

## API 接口

### 获取统计摘要
```bash
GET /api/logs/summary?days=7
```

### 获取日志文件列表
```bash
GET /api/logs/files?category=system&days=7
```

### 获取日志内容
```bash
GET /api/logs/content?category=system&date=2024-03-17&level=ERROR&limit=100
```

### 查看日志末尾
```bash
GET /api/logs/tail?category=access&date=2024-03-17&lines=100
```

### 搜索日志
```bash
GET /api/logs/search?keyword=error&days=7
```

### 下载日志文件
```bash
GET /api/logs/download/system/2024-03-17
```

### 清理旧日志
```bash
DELETE /api/logs/cleanup?keep_days=30
```

## 配置

```python
from neu_log import LogConfig, LogLevel

config = LogConfig(
    log_dir="data/logs",           # 日志目录
    level=LogLevel.INFO,            # 日志级别
    max_bytes=10*1024*1024,        # 单个文件最大 10MB
    backup_count=7,                 # 保留 7 个备份
    format_type="text",            # text 或 json
    console_output=True,            # 同时输出到控制台
)
```

## 日志格式

### 文本格式
```
2024-03-17 21:30:00 [INFO] neu.system.main: 服务启动成功
2024-03-17 21:30:01 [ERROR] neu.system.auth: 登录失败: 密码错误
```

### JSON 格式
```json
{
  "timestamp": "2024-03-17T21:30:00",
  "level": "INFO",
  "logger": "neu.system.main",
  "message": "服务启动成功",
  "module": "main",
  "function": "startup",
  "line": 42
}
```

## 最佳实践

1. **选择合适的分类** - 便于后续查找和过滤
2. **使用适当的级别** - DEBUG 用于调试，ERROR 用于需要关注的错误
3. **添加上下文信息** - 使用 StructuredLogger 记录关键上下文
4. **定期清理** - 设置合理的保留策略，避免磁盘占满
5. **关注错误日志** - 定期检查 error 分类的日志
