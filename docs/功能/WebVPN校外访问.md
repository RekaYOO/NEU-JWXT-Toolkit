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
| `backend/core/auth/recovery.py` | 成绩追踪与自动选课共享的一次性远程认证恢复上下文、签名令牌和内存 Flow。 |
| `backend/core/notifications/mail.py` | 全系统 SMTP 配置、持久化 outbox、去重和失败重试。 |
| `backend/app/routers/auth.py` | 对前端提供本地认证 API。 |
| `backend/app/routers/auth_recovery.py` | 对一次性恢复页面提供 token-scoped API。 |
| `frontend/src/pages/LoginPage.js` | 账号密码、二维码面板切换、二维码轮询和短信二次认证弹窗。 |
| `frontend/src/pages/AuthRecoveryPage.js` | 邮件中的独立恢复页面，按上下文续接二维码或短信流程。 |

`NEUAuthClient` 在 `webvpn` 模式下通常会将 `*.neu.edu.cn` 教务业务请求改写为 WebVPN
URL；同类 `Referer` 也会改写，`Origin` 会改为 WebVPN 源站。受控服务请求是例外：
`request_service(...)` 统一复用当前 CAS 身份和底层 Session，为登记过的校园业务系统建立
各自的业务会话。`cxcy` 始终直连；`jwxk` 可按服务级设置跟随、直连或使用 WebVPN。服务级
线路覆盖不会修改教务系统的 `active_mode`，也不会创建第二份凭据或 Cookie 文件。

## 登录流程

### 微信扫码快速登录

1. 前端调用 `POST /api/webvpn/qr/start`，后端创建一个仅在内存中保存的扫码流程。
2. 后端返回 CAS 官方二维码内容、`flow_id`、轮询间隔和过期时间。
3. 前端每隔 `poll_interval` 秒调用 `POST /api/webvpn/qr/status`。
4. 扫码成功后，后端完成 CAS 回调并同步 WebVPN 所需的认证 Cookie。普通登录通过教务系统
   当前用户接口校验；选课页传入 `target_service=jwxk` 时改为核验 JWXK 服务入口。
5. 校验成功后将 Cookie 保存到本地 `data/session.json`。

二维码流程有效期为 180 秒。`flow_id` 仅存在于后端内存；后端重启、取消流程或过期后，必须重新获取二维码。

### WebVPN 账号密码与短信二次认证

1. 前端调用 `POST /api/webvpn/password/start`。
2. 后端打开 WebVPN 代理的 CAS 登录页，提取隐藏字段和 RSA 公钥，按官网表单格式提交账号密码。
3. 若统一认证页面返回 `form#second_auth_form`，后端提取页面地址和隐藏字段，返回 `status: "sms_required"`、短时有效的 `flow_id` 和验证码图片。
4. 二次认证页面中的图片元素可能是预览图，不能作为验证码端点。后端固定从该页面所在目录请求
   `code?vpn-1&<随机数>`，沿用当前 WebVPN Session 并带页面 Referer；后端会校验响应内容，优先按图片
   magic bytes 修正学校偶尔出现的 `Content-Type: image/jpeg` 但实际为 GIF 的情况，再生成可显示的 data URL。
5. 用户手动填写图形验证码后，明确点击按钮调用 `POST /api/webvpn/sms/send`。WebVPN 网关要求短信接口使用
   `?vpn-12-o2-pass.neu.edu.cn`。`secondAuthCode` 请求体只接收
   `code=<图形验证码>&method=mobile`。短信发送失败或图形验证码错误时会刷新图片并保留当前 Flow。
6. 用户输入短信验证码后调用 `POST /api/webvpn/sms/verify`，后端提交二次认证表单并校验教务系统会话。

短信分支由统一认证服务决定；某些账户、设备授信状态或保护期不会触发该分支。未触发并不代表项目跳过了短信流程。待提交表单只存在于内存中，短信验证码按学校约五分钟的有效时间展示；服务器额外保留一分钟请求容错，避免浏览器调度、时钟差或在途请求导致本地提前拒绝，最终有效性仍由学校接口判断。表单绝不会写入 `session.json`、日志或接口响应。

账号密码校验失败时，学校页面可能分别返回“账号不存在”或“密码错误”。工具箱对外统一
显示“账号或密码错误”，避免依据远端提示误判具体是哪一项有误。

## 本地认证 API

所有接口由本地 FastAPI 服务提供，默认地址为 `http://localhost:8000`。接口请求和响应均使用 JSON。

### 状态与直连登录

| 方法和路径 | 请求字段 | 关键响应字段 |
| --- | --- | --- |
| `GET /api/status` | 无 | `is_logged_in`、`has_credentials`、`has_local_data`、`current_user`、`network_mode`、`storage` |
| `POST /api/login` | `username`、`password`、`remember=false`、`network_mode="direct"` | `success`、`message`、`username`、`requires_webvpn`、`network_mode`、`error_code`、`suggestion` |
| `POST /api/logout?clear_data=true` | 查询参数 `clear_data` | `success`、`data_cleared`、`cleared_files` |

`network_mode` 只接受 `direct` 或 `webvpn`。登录页在 WebVPN 模式下使用下述专用接口，而不是 `/api/login`。

`GET /api/auth/pending` 只读取当前进程内是否存在等待前台处理的图形验证码挑战，不触发远端请求，
响应不包含密码、Cookie 或验证码原文；课表和选课后台任务遇到此状态会暂停静默恢复，等待用户在
当前页面完成认证。成绩追踪和自动选课可调用独立远程恢复服务发送通知：未配置重新登录地址时提示
进入工具箱手动登录；配置地址且保存账密已进入同目标短信挑战时，一次性页面直接续接该 Session，
否则先提供二维码重新登录。

Cookie 和已保存账密的自动恢复由进程级认证管理器统一退避。一次完整恢复链路失败后，所有页面、
缓存刷新和后台任务共享同一个冷却状态，首次等待 30 秒，连续失败逐步延长，最长 5 分钟；冷却期间
状态读取不得再次访问 CAS。成功登录、显式退出或切换账号会清除该状态。若恢复进入图形验证码/短信
阶段，则保留原 Session 等待用户处理，不再按定时轮询重新提交账密。

`/api/login` 的错误字段含义：

| 字段 | 含义 | 推荐处理 |
| --- | --- | --- |
| `WRONG_PASSWORD` | 账号或密码无法通过统一认证 | 检查学号和密码。 |
| `DIRECT_ACCESS_FAILED` | 校内直连不可达、超时或被导向 WebVPN | 检查校园网络；校外切换 WebVPN。 |
| `REQUEST_ERROR` | 页面结构、协议或其他请求异常 | 查看日志后重试。 |

`suggestion` 是面向界面的简短处理建议；客户端不应依赖完整的中文 `message` 判断业务状态。

直连 CAS 首次连接失败或超时后立即返回 `DIRECT_ACCESS_FAILED`，不在一次前台登录请求内
把完整认证流程重试三轮。登录表单发送后发生网络异常时，结果视为不确定，不当作 RSA 公钥
失效继续刷新密钥或重放表单；只有收到明确认证拒绝后的原有公钥核验流程保留。
后台自动恢复仍由 `AuthSessionManager` 统一退避。以上规则由共享后端实现，网页、桌面和
Android 本地版一致；Android 原生桥也必须使用 Axios 的请求期限，不能额外提前中断响应。

WebVPN 专用接口在保留 `message` 和 `status` 的同时返回稳定的 `error_code`。前端应优先按代码分支，
中文消息只用于展示：

| `error_code` | 含义 | 前端处理 |
| --- | --- | --- |
| `WEBVPN_FLOW_MISSING` | 服务端没有对应的内存流程 | 关闭当前弹窗/二维码，提示重新开始登录 |
| `WEBVPN_FLOW_REPLACED` | 流程已被另一轮登录替换 | 丢弃旧表单状态，提示重新开始 |
| `WEBVPN_FLOW_EXPIRED` | 二维码已过期，或短信 Flow 超过五分钟预计窗口及一分钟请求容错 | 关闭旧流程，要求重新输入账号密码 |
| `WEBVPN_CAPTCHA_FETCH_FAILED` | 图形验证码缺失、空响应或无法识别为图片 | 保留页面，允许刷新或重新开始 |
| `WEBVPN_CAPTCHA_INVALID` | 图形验证码为空或官方校验失败 | 保留弹窗，使用返回的新图片重新填写 |
| `WEBVPN_SMS_RATE_LIMITED` | 官方短信发送限流 | 不刷新登录流程，等待官方冷却 |
| `WEBVPN_SMS_PHONE_UNBOUND` | 统一认证未绑定可用手机号 | 停止短信流程，改用扫码或联系学校 |
| `WEBVPN_SMS_INVALID` | 短信验证码错误或过期 | 保留当前流程，允许重新输入 |
| `WEBVPN_UPSTREAM_TIMEOUT` | 学校接口超时 | 不重放写请求，稍后重试 |
| `WEBVPN_UPSTREAM_NON_JSON` | 官方状态接口返回非 JSON | 结束当前轮询/保留表单，提示重新开始 |
| `WEBVPN_UPSTREAM_REDIRECT` | 跳转不在受控官方路径 | 停止流程并要求重新登录 |
| `WEBVPN_SESSION_ESTABLISH_FAILED` | 认证完成但教务会话未建立 | 不标记成功，提示重新认证 |
| `WEBVPN_UNKNOWN_ERROR` | 未分类的上游或本地异常 | 保留错误编号，查看脱敏日志 |

错误响应仍使用 HTTP 200 以兼容既有前端；`success=false`、`status` 和 `error_code` 才是机器可读结果。

### 二维码接口

| 方法和路径 | 请求字段 | 成功响应/状态 |
| --- | --- | --- |
| `POST /api/webvpn/qr/start` | `username`（可选）、`target_service=primary|jwxk`（默认 `primary`） | `success`、`flow_id`、`qr_content`、`expires_in`、`poll_interval` |
| `POST /api/webvpn/qr/status` | `flow_id` | `pending`、`sms_required`、`authenticated`、`expired` 或 `error` |
| `POST /api/webvpn/qr/cancel` | `flow_id` | `success` |

当状态为 `authenticated` 时，响应可带 `username`。状态为 `error` 时会额外给出 `diagnostics`；其中只包含跳转主机、路径、查询参数名、响应状态和 Cookie 名称等脱敏诊断信息，不含 Cookie 值、票据或二维码 UUID。

### WebVPN 密码和短信接口

| 方法和路径 | 请求字段 | 成功响应/状态 |
| --- | --- | --- |
| `POST /api/webvpn/password/start` | `username`、`password`、`remember=false`、`target_service=primary|jwxk`（默认 `primary`） | `status: "authenticated"` 或 `status: "sms_required"`；后者附带 `flow_id`、`expires_in` |
| `POST /api/webvpn/sms/captcha/refresh` | `flow_id` | 学校实时返回的新验证码图片，并以 `expires_in` 重新给出约五分钟预计窗口 |
| `POST /api/webvpn/sms/send` | `flow_id`、`captcha_code` | `status: "sent"`、`expires_in`；只在用户明确点击后发送，成功重发会重置预计窗口 |
| `POST /api/webvpn/sms/verify` | `flow_id`、`code`、`trust_device=false` | `status: "authenticated"`、`username`、`message` |
| `POST /api/webvpn/sms/cancel` | `flow_id` | `success` |

成绩追踪和自动选课任务共享的一次性恢复页使用以下独立接口：

| 方法和路径 | 请求字段 | 用途 |
| --- | --- | --- |
| `GET /api/auth-recovery/{token}/status` | 无 | 只读当前上下文及进程内 Flow 状态 |
| `POST /api/auth-recovery/{token}/start` | 无 | 续接已有短信挑战，或创建新的二维码 Flow |
| `GET /api/auth-recovery/{token}/poll` | 无 | 轮询二维码，并在官方要求时切换到短信阶段 |
| `POST /api/auth-recovery/{token}/captcha/refresh` | 无 | 从当前绑定 Session 刷新图形验证码 |
| `POST /api/auth-recovery/{token}/sms/send` | `captcha_code` | 用户确认图形验证码后主动发送/重发短信 |
| `POST /api/auth-recovery/{token}/sms/verify` | `code`、`trust_device=false` | 提交短信验证码并完成目标服务认证 |
| `POST /api/auth-recovery/{token}/cancel` | 无 | 取消 Flow 并立即使本链接失效 |

授权边界是邮件中的高强度签名 token。它只能操作该 token 绑定的候选 Session，不能访问未绑定的
其他登录流程；成功、取消、主动退出、账号切换或到期后 token 立即失效。链接默认有效 3 小时，
可在系统设置调整新链接的有效期，已创建链接的截止时间不会因刷新、重开 Flow 或重启服务延长。二维码、图形验证码、短信 Flow、
Cookie 和候选 Session 只在内存中存在；持久化状态只保存上下文 ID 与 token 哈希，邮件 outbox 只
保存模板和上下文 ID，真正发送时才生成 URL。服务重启后未到期的已发送链接仍能重新开始二维码流程，但不会
恢复已经丢失的内存验证码 Flow。

恢复链接同时绑定目标服务：成绩追踪使用 `primary`，自动抢课、空位换课和策略投权使用 `jwxk`。
`primary` 成功后原子接管主认证 Session；`jwxk` 成功后只合并 WebVPN 网关 Cookie 和 JWXK 子会话，
或在主会话尚未恢复时保存为自动任务专用候选，不改变主 JWXT 的直连/WebVPN 选择。旧
`/grade-tracking/recovery/` 页面和 `/api/grade-tracking/recovery/` API 已删除，升级前的旧链接
直接失效，也不再绕过 Linux 网站访问密码。

验证码响应中的关键字段：

| 字段 | 含义 |
| --- | --- |
| `captcha_image` | 当前图形验证码的 data URL，仅用于当前弹窗显示 |

短信接口的典型 `message`：

| 返回情况 | 含义 |
| --- | --- |
| `发送过于频繁，请稍后再试` | 官方 `secondAuthCode` 接口限流。 |
| `统一认证未绑定手机号码` | 官方接口返回 `unknow`。 |
| `验证码有误` | 官方接口返回 `codeErr`。 |
| `验证码已超时` | 官方接口返回 `timeout`。 |
| 其他未分类官方状态（例如 `most`） | 当前不猜测其业务含义，返回 `WEBVPN_UNKNOWN_ERROR` 并保留脱敏错误信息；不会自动解除设备或重放短信请求。 |

## 会话恢复与登出

### 校园网环境下的 WebVPN 拒绝

学校网关明确规定 WebVPN 仅供校外访问。在校园网中访问 `webvpn.neu.edu.cn` 时，
可能返回 HTTP 403，并显示“访问被拒绝”以及“校园网用户无需使用WebVPN”等官方提示。
客户端只在响应主机为 `webvpn.neu.edu.cn`、状态为 403 且正文命中这些固定文案时，
将其归类为 `WEBVPN_CAMPUS_NETWORK_BLOCKED`；普通 403 不作此推断。

登录页遇到该错误会停止二维码、账号密码或短信流程，回到账号密码视图并提示切换为
“校内直连”，不会自动提交直连账号密码。已有 WebVPN 会话在恢复、验证码、短信或业务
读取中遇到该错误时，保留前台页面和已有缓存，停止继续重试；选课页提供“直连”和
“跟随教务”线路切换。只有用户明确切换线路并重新操作后，系统才会访问校内直连服务。
该错误不会被当作普通密码错误、验证码错误，也不会触发短信重放或选课写操作重放。

登录成功后，认证 Cookie 保存为 `data/session.json`。文件包含：

| 字段 | 含义 |
| --- | --- |
| `version` | 会话文件格式版本。 |
| `username` | 用于核对会话所属账户。 |
| `active_mode` | `direct` 或 `webvpn`，保证服务重启后仍采用正确的访问路径。 |
| `cookies` | Cookie 名称、域、路径、过期时间和敏感 Cookie 值。 |
| `saved_at` | 保存时间戳。 |

启动后优先检查内存会话，再尝试 `session.json` 中的 Cookie；WebVPN 会话会通过教务系统当前用户接口验证。Cookie 已过期或被服务器撤销时，如果本地保存了匹配账号的密码，直连和 WebVPN 都会先在后端串行边界内静默尝试一次账号密码恢复。WebVPN 恢复若进入短信验证，会把产生挑战的同一个客户端保存为“待认证候选 Session”并交给前台弹窗；后续页面读取和后台任务等待该 Flow 完成、取消或过期，不得再次提交密码表单或替换验证码。系统不会自动发送短信。Flow 过期或用户取消后，才允许下一次正常恢复。

静默恢复成功时，原业务请求继续执行，前端不会被踢回登录页；恢复失败时才返回未认证状态。本地存在可离线读取的缓存时，前端询问用户进入只读离线模式还是重新登录；没有缓存时直接返回登录页。该降级不会调用清理数据的登出接口，也不会删除凭据、缓存或用户文档。

前端收到普通 JWXT 业务接口 `401` 时，先通过 `/api/status` 触发一次静默恢复，
并在成功后自动重试原请求一次。JWXK 使用独立的服务级恢复范围：其只读接口
发生 `401` 时检查 `/api/course-selection/jwxk/status` 的
`service_authenticated`，不能用 JWXT 的 `is_logged_in` 代替。JWXK 子会话恢复失败
只影响选课系统当前远端读取，不会触发全局“教务会话已失效”或把用户踢出其他页面；
本地目录和方案仍可继续查看。后台容量、人数等静默刷新失败也只保留旧数据。
多个并发请求按恢复范围共用同一次恢复，原请求最多重试一次。已确认身份后的只读请求可在
四个并发槽内并行网络访问；每个请求使用主 Cookie 的隔离快照，响应 Cookie 只有在未发生并发
更新冲突时才合并回主 Session。认证恢复本身仍由客户端单飞锁保护，新的认证、验证码、
退出或任何写操作继续走独占边界，避免重复启动恢复链和短信 Flow 互相污染。用户主动调用退出接口后
不会执行这一恢复流程。

短信二次认证的登录页、选课页、全局恢复弹窗和邮件恢复页面复用同一个
`WebVPNAuthFields`。桌面弹窗将验证码图片、图形验证码输入和刷新按钮组成一行；窄屏改为
“图片 + 刷新”与整行输入框，短信输入和发送按钮保持紧凑并排。弹窗正文可独立滚动，标题、
关闭入口和底部验证按钮不随正文滚出；手机虚拟键盘出现时使用 `visualViewport` 的实际高度
重新限制弹窗，而不是依赖固定 `100vh`。320px 窄屏和低高度横屏会进一步压缩间距，但不会
隐藏验证码、刷新、发送、重发或最终提交能力。

JWXK 的 `token` Cookie 会同时映射到业务请求的 `Authorization` 头。映射时必须按当前
服务线路和实际 API 路径选择 Cookie：直连请求只使用 `jwxk.neu.edu.cn` 的 token，WebVPN
请求只使用代理域的 token；`/xsxk/profile` 等旧路径 Cookie 不能覆盖适用于
`/xsxk/elective`、`/xsxk/volunteer` 的 `/xsxk` token。只读请求确认 token 被拒绝后，恢复
流程会先删除当前线路的旧 token，再执行 CAS 回调并确认确实取得新 token，不能仅凭“最终回到
JWXK 页面”或 Cookie Jar 中仍存在同名旧值就判定成功。JWXK 子会话失效不会把全局 JWXT
登录标记改成失效；自动任务也不会为了每轮 JWXK 检查先探测一次 JWXT。若 JWXT 当前线路不可达
但 JWXK 直连可用，保存了凭据时允许在同一 Session 内直接针对 JWXK CAS 服务恢复身份。
WebVPN 静默恢复需要短信时仍停止自动恢复并交给用户完成，不会在后台擅自发送验证码。

JWXK 恢复固定从其服务入口 `/xsxk/auth/cas` 发起，再由该入口跳转到 `pass.neu.edu.cn` 并回到
JWXK；不能只从通用教务登录状态推断选课系统是否可用。JWXK 的部分不适用结果源也会返回业务
`401`，只有响应明确包含登录、认证或 token 失效语义，或者实际返回登录 HTML/认证跳转时，才按
子会话失效处理。即使 JWXT 的全局登录标记暂时不可用，只要当前账号的 JWXK token 仍有效，后台
自动任务仍可继续读取；token 被服务端拒绝后才进入上述服务级恢复。

选课系统主页选择 WebVPN 作为线路后，会单独检查 JWXK 的 WebVPN 子会话。即使主教务当前
使用直连，只要存在同账号本地凭据，也会在 JWXK 的 WebVPN CAS 跳转不可用后自动尝试一次
WebVPN 账密登录；该过程保留直连主线路及其非 WebVPN Cookie，只把新取得的网关 Cookie
合并回共享 Session。Cookie 恢复的客户端若缺少内存密码，会在账号一致时先挂载本地已保存
凭据。官方进入验证码、短信保护页或账密恢复失败时，静默流程立即停止，不会在后台擅自处理
验证码。用户可直接在选课主页选择账号密码或微信扫码；两者出现二次认证时复用同一个验证码/
短信弹窗，成功后页面自动重新读取账号资格、轮次和课程目录，无需跳转到登录页。

二维码必须从没有既有 CAS 身份的干净 Session 打开，否则统一认证会直接跳转而不会生成二维码。
系统因此将它保存为“待认证候选 Session”：普通登录成功后原子替换活动身份；JWXK 定向恢复
成功后只把候选 Session 的 WebVPN 网关 Cookie 合并到当前同账号活动 Session，不改变主教务
线路。取消或失败只丢弃候选 Session；候选 Session 不得被课程、成绩或后台任务用于业务请求。

对 `target_service=jwxk`，CAS/短信表单成功和 JWXK 可用性是两个结果：验证码提交成功后即保留
新 WebVPN 网关会话；随后 JWXK 超时、未开放或没有服务 token 时由选课状态接口显示为服务
不可用，不得把它回退成“短信验证失败”并要求用户重复获取验证码。

JWXK 的 WebVPN CAS 链可能从代理后的 `/xsxk/auth/cas` 返回普通的 `http://pass.neu.edu.cn/tpass/login`，CAS 又返回普通 JWXK 回调地址。客户端只接受官方 CAS 精确路径和登记过的 JWXK 主机，先将明文 CAS 跳转升级为 HTTPS，再把 CAS 与 JWXK 回调转换回 WebVPN URL；不会放宽到其他主机或通过 HTTP 发送 Cookie、ticket。

WebVPN 不会把上游 JWXK 的 `token` 直接写入本地 HTTP Cookie Jar；浏览器端由网关注入脚本通过 `/wengine-vpn/cookie?method=get` 读取虚拟 Cookie。后端在 WebVPN 模式下复用同一官方机制，从受控的 JWXK 主机和请求路径读取虚拟 `token`，只在内存中缓存并映射为 `Authorization`。直连模式仍读取 `jwxk.neu.edu.cn` 的真实 Cookie，二者不能混用。

截至 2026 年 8 月 15 日，JWXK 的 HTTPS 服务入口实际会返回指向
`http://pass.neu.edu.cn/tpass/login` 的绝对重定向。客户端只对“官方统一认证域名 + 精确
`/tpass/login` 路径 + 标准 HTTP 端口”这一种情况在发送下一跳前强制升级为 HTTPS；不会真的通过
明文 HTTP 发送 Cookie 或 CAS ticket，也不会因此放宽其他不受信任跳转。

`session.json` 未加密，应仅保存在受信任的本地用户目录中，且绝不可分享或提交。勾选“记住密码”还会启用本地自动登录配置；公共设备不建议勾选。

`POST /api/logout` 会取消未完成的二维码/短信流程、清除内存 Cookie、删除持久化会话，并可按 `clear_data` 清理本地业务数据。
在 Linux 私有部署中，网站访问密码页与 NEU 账号页使用不同的浏览器自动填充分区和字段名，
避免密码管理器把服务器访问密码填入 NEU 密码框。后端还会对学号执行 Unicode NFKC 规范化并
去除首尾空白（密码保持原样），全角数字和移动端复制产生的首尾空格不会被提交给统一认证；
学号内部空白或控制字符会在本地直接拒绝。

登出会先以最高优先级在共享远端 Session 队列中建立屏障，再立即撤销内存身份和提升 identity
epoch。正在执行的单个远端请求不会被强制中断，但尚未执行的登录和读取不能越过登出清理；
若旧认证请求在首次撤销后才返回，登出会再次撤销其迟到结果。这样可避免 Linux 上学校接口缓慢
时，旧登出在用户重新登录后删除新保存的 Cookie、凭据或短信 Flow。

用户主动退出或退出离线模式时，前端在当前标签页的 `sessionStorage`
写入主动退出标记。App 启动检查、登录页状态检查和业务 `401` 恢复都会
尊重该标记，不再静默登录；密码、二维码或短信明确登录成功后清除标记。
因此同一标签页刷新仍保持退出状态，关闭窗口或打开新标签页视为新的使用
会话，可以继续按“记住登录”设置自动恢复。

## 日志与排障

图形验证码被官方拒绝时，后端刷新图片后仍返回 `success=false`、
`status=captcha_invalid`、`captcha_invalid=true` 和 `WEBVPN_CAPTCHA_INVALID`；
图片刷新结果不得覆盖此次操作的失败状态。普通登录、全局认证恢复、选课与邮件恢复页面
保留弹窗/流程，展示新图并清空图形及短信旧输入，只有用户重新点击获取才发送短信。
新图片加载超时或失败时清除旧图，提示手动刷新；没有有效图片时发送按钮不可用。
明确的短信验证码错误只提示短信错误，不刷新图片或自动重发短信。

短信成功后应立即完成身份接管并让当前页面显示成功，不需要刷新网页。远程恢复服务的
成功清理只撤销内存 Flow，不嵌套申请远端 Session 锁；排队等待时也不占住恢复上下文锁，
避免连带阻塞课表、成绩等读取。

邮件恢复页面区分“短信 Flow 已失效”和“链接已失效”：前者使用同一链接重新开始登录，
后者不能继续使用。页面不会把短信发送失败的响应显示为“已发送”，成功状态也不会被
此前尚未返回的轮询结果覆盖。重新打开仍有效的短信挑战会保留它，刷新图片和手动重发
仍走原有接口；后台不会自动发短信。

日志位于 `data/logs/`，该目录和所有会话文件均被 `.gitignore` 排除。WebVPN 密码、验证码与短信流程记录：

- 登录页和表单提交的 HTTP 状态、最终跳转主机与路径；
- 是否检测到二次认证表单；
- 官方 `secondAuthCode` 的状态和返回字段名；
- 验证码图片请求是否返回受支持的图片类型；
- 最终会话校验结果。

日志不得记录密码、图形验证码、短信验证码、验证码图片、Cookie 值、CAS ticket、二维码 UUID 或完整重定向查询串。排障时可提供相关时间段的脱敏日志，不要直接分享 `data/session.json`。

## 开发注意事项

- WebVPN URL 的主机加密规则集中在 `WebVPNUrlCodec`；网关规则变化时只修改该模块。
- 二维码和短信 `flow_id` 是单进程内存状态。部署多个后端进程时，需要使用粘性会话或共享的短期状态存储。
- 对校内服务增加新 API 时，应继续向业务层传递原始校内 URL，由 `NEUAuthClient._session_request()` 统一改写，不要在每个业务模块手工拼接 WebVPN 地址。
- 跨业务系统必须通过代码登记的 service 白名单接入。service 固定主机、CAS 回调和允许
  路径，不接受调用方提供任意 URL；所有服务仍共享当前 Session、恢复流程和远端互斥。
- 认证接口只用于本机服务；不要暴露到公网，也不要将 `data/` 目录纳入版本控制。
