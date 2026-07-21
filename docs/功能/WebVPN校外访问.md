# WebVPN 校外访问与认证

本模块让本地运行的工具箱在校外通过东北大学 WebVPN 访问教务系统。它只代理应用后端发往校内服务的请求，不会把前端页面部署到 WebVPN。

## 访问方式

| 模式 | 适用网络 | 认证方式 | 说明 |
| --- | --- | --- | --- |
| `direct` | 校园网 | 学号和密码 | 直接访问教务系统。连接慢、超时或被导向认证页时，界面会建议改用 WebVPN。 |
| `webvpn` | 校外网络 | 微信扫码（推荐）或账号密码 | 所有目标为 `*.neu.edu.cn` 的业务请求会自动转为 WebVPN 地址。 |

项目不再提供“自动”访问模式：网络环境不能由客户端可靠判断，用户应在登录页明确选择校内直连或 WebVPN。

## 模块组成

| 位置 | 职责 |
| --- | --- |
| `backend/core/network/webvpn.py` | 将普通 HTTP/HTTPS 地址转换为东北大学 WebVPN 地址。保留路径、查询参数、片段和显式端口。 |
| `backend/core/auth/client.py` | CAS 直连认证、WebVPN 二维码/短信状态机、Cookie 恢复和校内请求自动改写。 |
| `backend/app/routers/auth.py` | 对前端提供本地认证 API。 |
| `frontend/src/pages/LoginPage.js` | 账号密码、二维码面板切换、二维码轮询和短信二次认证弹窗。 |

`NEUAuthClient` 在 `webvpn` 模式下会将 `*.neu.edu.cn` 请求改写为 WebVPN URL；同类 `Referer` 也会改写，`Origin` 会改为 WebVPN 源站。非东北大学域名不会被改写。

## 登录流程

### 微信扫码快速登录

1. 前端调用 `POST /api/webvpn/qr/start`，后端创建一个仅在内存中保存的扫码流程。
2. 后端返回 CAS 官方二维码内容、`flow_id`、轮询间隔和过期时间。
3. 前端每隔 `poll_interval` 秒调用 `POST /api/webvpn/qr/status`。
4. 扫码成功后，后端完成 CAS 回调、同步 WebVPN 所需的认证 Cookie，并通过教务系统当前用户接口校验会话。
5. 校验成功后将 Cookie 保存到本地 `data/session.json`。

二维码流程有效期为 180 秒。`flow_id` 仅存在于后端内存；后端重启、取消流程或过期后，必须重新获取二维码。

### WebVPN 账号密码与短信二次认证

1. 前端调用 `POST /api/webvpn/password/start`。
2. 后端打开 WebVPN 代理的 CAS 登录页，提取隐藏字段和 RSA 公钥，按官网表单格式提交账号密码。
3. 若统一认证页面返回 `phone(murmur, details)`，后端返回 `status: "sms_required"` 和短时有效的 `flow_id`。
4. 前端调用 `POST /api/webvpn/sms/send` 请求发送短信，再调用 `POST /api/webvpn/sms/verify` 校验验证码。
5. 校验成功后，后端重新提交原 CAS 表单并校验教务系统会话。

短信分支由统一认证服务决定；某些账户、设备授信状态或保护期不会触发该分支。未触发并不代表项目跳过了短信流程。待提交表单只存在于内存中、有效 180 秒，且绝不会写入 `session.json`、日志或接口响应。

## 本地认证 API

所有接口由本地 FastAPI 服务提供，默认地址为 `http://localhost:8000`。接口请求和响应均使用 JSON。

### 状态与直连登录

| 方法和路径 | 请求字段 | 关键响应字段 |
| --- | --- | --- |
| `GET /api/status` | 无 | `is_logged_in`、`has_credentials`、`has_local_data`、`current_user`、`network_mode`、`storage` |
| `POST /api/login` | `username`、`password`、`remember=false`、`network_mode="direct"` | `success`、`message`、`username`、`requires_webvpn`、`network_mode`、`error_code`、`suggestion` |
| `POST /api/logout?clear_data=true` | 查询参数 `clear_data` | `success`、`data_cleared`、`cleared_files` |

`network_mode` 只接受 `direct` 或 `webvpn`。登录页在 WebVPN 模式下使用下述专用接口，而不是 `/api/login`。

`/api/login` 的错误字段含义：

| 字段 | 含义 | 推荐处理 |
| --- | --- | --- |
| `WRONG_PASSWORD` | 账号或密码无法通过统一认证 | 检查学号和密码。 |
| `DIRECT_ACCESS_FAILED` | 校内直连不可达、超时或被导向 WebVPN | 检查校园网络；校外切换 WebVPN。 |
| `REQUEST_ERROR` | 页面结构、协议或其他请求异常 | 查看日志后重试。 |

`suggestion` 是面向界面的简短处理建议；客户端不应依赖完整的中文 `message` 判断业务状态。

### 二维码接口

| 方法和路径 | 请求字段 | 成功响应/状态 |
| --- | --- | --- |
| `POST /api/webvpn/qr/start` | `username`（可选） | `success`、`flow_id`、`qr_content`、`expires_in`、`poll_interval` |
| `POST /api/webvpn/qr/status` | `flow_id` | `pending`、`authenticated`、`expired` 或 `error` |
| `POST /api/webvpn/qr/cancel` | `flow_id` | `success` |

当状态为 `authenticated` 时，响应可带 `username`。状态为 `error` 时会额外给出 `diagnostics`；其中只包含跳转主机、路径、查询参数名、响应状态和 Cookie 名称等脱敏诊断信息，不含 Cookie 值、票据或二维码 UUID。

### WebVPN 密码和短信接口

| 方法和路径 | 请求字段 | 成功响应/状态 |
| --- | --- | --- |
| `POST /api/webvpn/password/start` | `username`、`password`、`remember=false` | `status: "authenticated"` 或 `status: "sms_required"`；后者附带 `flow_id`、`expires_in` |
| `POST /api/webvpn/sms/send` | `flow_id` | `status: "sent"` |
| `POST /api/webvpn/sms/verify` | `flow_id`、`code`、`trust_device=false` | `status: "authenticated"`、`username`、`message` |
| `POST /api/webvpn/sms/cancel` | `flow_id` | `success` |

短信接口的典型 `message`：

| 返回情况 | 含义 |
| --- | --- |
| `发送过于频繁，请稍后再试` | 官方 `device` 接口返回 `max`。 |
| `统一认证未绑定手机号码` | 官方接口返回 `unknow`。 |
| `验证码有误` | 官方接口返回 `codeErr`。 |
| `验证码已超时` | 官方接口返回 `timeout`。 |
| 设备数量已达上限… | 官方接口返回 `most`，系统已按官方逻辑解除最早的授信设备。 |

## 会话恢复与登出

登录成功后，认证 Cookie 保存为 `data/session.json`。文件包含：

| 字段 | 含义 |
| --- | --- |
| `version` | 会话文件格式版本。 |
| `username` | 用于核对会话所属账户。 |
| `active_mode` | `direct` 或 `webvpn`，保证服务重启后仍采用正确的访问路径。 |
| `cookies` | Cookie 名称、域、路径、过期时间和敏感 Cookie 值。 |
| `saved_at` | 保存时间戳。 |

启动后优先检查内存会话，再尝试 `session.json` 中的 Cookie；WebVPN 会话会通过教务系统当前用户接口验证。Cookie 已过期或被服务器撤销时，需要重新认证。该文件未加密，应仅保存在受信任的本地用户目录中，且绝不可分享或提交。勾选“记住密码”还会启用本地自动登录配置；公共设备不建议勾选。

`POST /api/logout` 会取消未完成的二维码/短信流程、清除内存 Cookie、删除持久化会话，并可按 `clear_data` 清理本地业务数据。

## 日志与排障

日志位于 `data/logs/`，该目录和所有会话文件均被 `.gitignore` 排除。WebVPN 密码与短信流程记录：

- 登录页和表单提交的 HTTP 状态、最终跳转主机与路径；
- 是否检测到短信挑战；
- 官方 `device` 接口的状态、`info` 值和返回字段名；
- 最终会话校验结果。

日志不得记录密码、短信验证码、Cookie 值、CAS ticket、二维码 UUID 或完整重定向查询串。排障时可提供相关时间段的脱敏日志，不要直接分享 `data/session.json`。

## 开发注意事项

- WebVPN URL 的主机加密规则集中在 `WebVPNUrlCodec`；网关规则变化时只修改该模块。
- 二维码和短信 `flow_id` 是单进程内存状态。部署多个后端进程时，需要使用粘性会话或共享的短期状态存储。
- 对校内服务增加新 API 时，应继续向业务层传递原始校内 URL，由 `NEUAuthClient._session_request()` 统一改写，不要在每个业务模块手工拼接 WebVPN 地址。
- 认证接口只用于本机服务；不要暴露到公网，也不要将 `data/` 目录纳入版本控制。
