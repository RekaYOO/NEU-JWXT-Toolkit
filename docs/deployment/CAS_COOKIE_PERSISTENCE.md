# CAS Cookie 持久化与票据刷新

## 概述

实现了一个智能的登录状态恢复机制，优先使用 CAS Cookie 刷新票据，避免频繁输入密码。

## CAS 登录机制

```
┌─────────────────────────────────────────────────────────────┐
│  用户首次登录                                                  │
│  ────────────                                                 │
│  1. 输入账号密码 → CAS 中心 (pass.neu.edu.cn)                 │
│  2. CAS 设置长期 Cookie (如 TPASSSESSIONID)                   │
│  3. 发放票据 (ticket) → 重定向到业务系统                      │
│  4. 业务系统建立本地 Session                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼ 票据过期/Session 失效
┌─────────────────────────────────────────────────────────────┐
│  传统方式（重新登录）                                          │
│  ─────────────────                                            │
│  业务系统 302 → CAS 登录页                                    │
│  用户重新输入账号密码                                          │
│  CAS 发放新票据 → 重定向回业务系统                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼ 新机制（免密刷新）
┌─────────────────────────────────────────────────────────────┐
│  Cookie 持久化机制                                            │
│  ────────────────                                             │
│  1. 保存 CAS Cookie 到本地文件 (data/session.pkl)             │
│  2. 票据失效时，携带 Cookie 访问 CAS                          │
│  3. CAS 识别有效 Cookie，直接发放新票据（无需密码）            │
│  4. 重定向回业务系统，恢复访问                                 │
└─────────────────────────────────────────────────────────────┘
```

## 使用方式

### 1. 创建客户端时启用 Cookie 持久化

```python
from neu_auth import NEUAuthClient

client = NEUAuthClient(
    username="学号",
    password="密码",
    cookie_file="data/session.pkl"  # 启用 Cookie 持久化
)

# 登录时会自动保存 CAS Cookie
client.login()
```

### 2. 自动恢复登录状态

```python
# 下次启动时，Cookie 会自动加载
client2 = NEUAuthClient(
    username="学号",
    password="密码",
    cookie_file="data/session.pkl"
)

# ensure_login 会按优先级尝试恢复：
# 1. 检查当前 Session 是否有效
# 2. 尝试用 Cookie 刷新票据（免密）
# 3. 用账号密码重新登录（兜底）
if client2.ensure_login():
    print("登录状态已恢复")
    # 可以继续使用，无需重新输入密码
```

### 3. 清除 Cookie

```python
# 登出时清除保存的 Cookie
client.clear_cookies()
```

## 后端集成

后端已自动集成此功能：

```python
# backend/main.py
COOKIE_FILE = os.path.join(_storage.config.data_dir, "session.pkl")

# 登录时启用 Cookie 持久化
client = NEUAuthClient(
    request.username, 
    request.password,
    cookie_file=COOKIE_FILE
)

# 自动登录管理器也使用 Cookie
_auto_login = AutoLoginManager(_storage, cookie_file=COOKIE_FILE)
```

### 登录恢复流程

```
用户访问 API
    │
    ▼
get_auth_client()
    │
    ├─> 检查内存中的 _auth_client（有效？）
    │   └─> 是 → 返回
    │
    ├─> 加载保存的凭证
    │   └─> 创建 NEUAuthClient(cookie_file=...)
    │       └─> 自动加载之前保存的 Cookie
    │
    ├─> client.ensure_login()
    │   ├─> 检查 Session 有效性
    │   ├─> 尝试用 Cookie 刷新票据（免密）
    │   └─> 用密码重新登录（兜底）
    │
    └─> 成功 → 返回客户端
```

## 状态恢复优先级

当需要登录时，系统按以下优先级尝试：

| 优先级 | 方式 | 是否需要密码 | 说明 |
|--------|------|--------------|------|
| 1 | 内存中的 Session | 否 | 服务未重启，Session 有效 |
| 2 | Cookie 刷新票据 | 否 | CAS Cookie 有效，免密获取新票据 |
| 3 | 重新登录 | 是 | Cookie 也失效了，需要重新输入密码 |

## Cookie 文件说明

- **位置**: `data/session.pkl`
- **格式**: pickle 序列化
- **内容**: 
  - `username`: 用户名（用于验证）
  - `cookies`: CAS 域下的所有 Cookie
  - `saved_at`: 保存时间戳
- **安全**: 只保存 Cookie，不保存密码

## 有效期说明

| 类型 | 有效期 | 说明 |
|------|--------|------|
| CAS Cookie | 通常 7-30 天 | 由 CAS 服务器设置 |
| 业务系统 Session | 通常 30 分钟-几小时 | 各系统不同 |
| 票据 (ticket) | 一次性使用 | 验证后立即失效 |

## 使用建议

1. **日常使用**: 勾选"记住密码"，Cookie 会自动处理票据刷新
2. **长时间未使用**: 如果超过几周没用，可能需要重新登录
3. **安全考虑**: 在公共电脑上使用后，记得点击"登出"清除 Cookie
4. **故障排查**: 如果自动登录失败，会回退到密码登录，不影响使用

## 与传统方式的对比

| 场景 | 传统方式 | Cookie 持久化 |
|------|----------|---------------|
| 后端服务重启 | 需要重新登录 | 自动恢复（免密） |
| Session 过期 | 需要重新登录 | Cookie 刷新（免密） |
| CAS Cookie 过期 | 需要重新登录 | 需要重新登录 |
| 多设备登录 | 各自独立 | 各自独立（Cookie 不共享） |

## 注意事项

1. **Cookie 不是永久有效** - CAS Cookie 也有过期时间（通常几天到几周）
2. **服务器端可能失效** - 服务端可以主动使 Cookie 失效
3. **不保存密码** - Cookie 文件只包含认证凭据，不包含明文密码
4. **文件权限** - `session.pkl` 包含敏感信息，注意文件权限设置
