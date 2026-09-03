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
4. 扫码成功后，后端完成 CAS 回调、同步 WebVPN 所需的认证 Cookie，并通过教务系统当前用户接口校验会话。
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

`GET /api/auth/pending` 只读取当前进程内是否存在等待前台处理的图形验证码挑战，不触发远端请求，
响应不包含密码、Cookie 或验证码原文；课表和选课后台任务遇到此状态会暂停静默恢复，等待用户在
当前页面完成认证。成绩追踪还会发送一次登录恢复通知：未配置重新登录地址时提示进入工具箱手动登录；
配置地址且保存账密已进入短信挑战时，一次性页面直接续接该 Session，否则先提供二维码重新登录。

`/api/login` 的错误字段含义：

| 字段 | 含义 | 推荐处理 |
| --- | --- | --- |
| `WRONG_PASSWORD` | 账号或密码无法通过统一认证 | 检查学号和密码。 |
| `DIRECT_ACCESS_FAILED` | 校内直连不可达、超时或被导向 WebVPN | 检查校园网络；校外切换 WebVPN。 |
| `REQUEST_ERROR` | 页面结构、协议或其他请求异常 | 查看日志后重试。 |

`suggestion` 是面向界面的简短处理建议；客户端不应依赖完整的中文 `message` 判断业务状态。

WebVPN 专用接口在保留 `message` 和 `status` 的同时返回稳定的 `error_code`。前端应优先按代码分支，
中文消息只用于展示：

| `error_code` | 含义 | 前端处理 |
| --- | --- | --- |
| `WEBVPN_FLOW_MISSING` | 服务端没有对应的内存流程 | 关闭当前弹窗/二维码，提示重新开始登录 |
| `WEBVPN_FLOW_REPLACED` | 流程已被另一轮登录替换 | 丢弃旧表单状态，提示重新开始 |
| `WEBVPN_FLOW_EXPIRED` | 图形验证码/短信流程超过 180 秒 | 关闭旧流程，要求重新输入账号密码 |
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
| `POST /api/webvpn/qr/start` | `username`（可选） | `success`、`flow_id`、`qr_content`、`expires_in`、`poll_interval` |
| `POST /api/webvpn/qr/status` | `flow_id` | `pending`、`sms_required`、`authenticated`、`expired` 或 `error` |
| `POST /api/webvpn/qr/cancel` | `flow_id` | `success` |

当状态为 `authenticated` 时，响应可带 `username`。状态为 `error` 时会额外给出 `diagnostics`；其中只包含跳转主机、路径、查询参数名、响应状态和 Cookie 名称等脱敏诊断信息，不含 Cookie 值、票据或二维码 UUID。

### WebVPN 密码和短信接口

| 方法和路径 | 请求字段 | 成功响应/状态 |
| --- | --- | --- |
| `POST /api/webvpn/password/start` | `username`、`password`、`remember=false` | `status: "authenticated"` 或 `status: "sms_required"`；后者附带 `flow_id`、`expires_in` |
| `POST /api/webvpn/sms/captcha/refresh` | `flow_id` | 学校实时返回的新验证码图片 |
| `POST /api/webvpn/sms/send` | `flow_id`、`captcha_code` | `status: "sent"`；只在用户明确点击后发送 |
| `POST /api/webvpn/sms/verify` | `flow_id`、`code`、`trust_device=false` | `status: "authenticated"`、`username`、`message` |
| `POST /api/webvpn/sms/cancel` | `flow_id` | `success` |

成绩追踪的一次性恢复页通过
`/api/grade-tracking/recovery/{token}/captcha/refresh`、`sms/send`、`sms/verify` 和 `cancel`
复用同一组官方验证码与短信操作，但授权边界是邮件中的高强度一次性 token。它只能操作该 token
绑定的候选 Session，不能访问未绑定的其他登录流程；成功后 token 立即失效。若保存账密的后台
恢复已经进入短信页，该候选 Session 会被明确绑定到恢复 token，链接不再要求重复扫码；没有可续接
挑战时才从二维码重新登录开始。

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
多个并发请求按恢复范围共用同一次恢复，原请求最多重试一次。用户主动调用退出接口后
不会执行这一恢复流程。

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
凭据。官方进入验证码、短信保护页或账密恢复失败时，静默流程立即停止并提示扫码，不会在后台
擅自处理验证码。用户可直接在选课主页弹出的二维码中完成认证，成功后页面自动重新读取账号
资格、轮次和课程目录，无需跳转到登录页。

二维码必须从没有既有 CAS 身份的干净 Session 打开，否则统一认证会直接跳转而不会生成二维码。系统因此将它保存为“待认证候选 Session”：扫码成功前业务请求仍使用原活动 Session，扫码成功后才原子替换活动身份；取消或失败只丢弃候选 Session，不影响原有登录。候选 Session 不得被课程、成绩或后台任务用于业务请求。

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
