package application

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const sessionCookie = "neu_toolbox_session"

type loginWindow struct {
	start    time.Time
	attempts int
}

type API struct {
	desktopHost         string
	desktopToken        string
	desktopShutdown     func()
	desktopShutdownOnce sync.Once
	releaseChecker      runtimeUpdateChecker
	database            *store.Store
	accounts            *Accounts
	version             string
	limitMutex          sync.Mutex
	loginWindows        map[string]loginWindow
	SecureCookies       bool
	resources           *Resources
	detailStates        sync.Map
	outlineStates       sync.Map
	filterCatalogs      sync.Map
	mailScanTimes       sync.Map
	trackingChecks      sync.Map
	remoteRecoveryLocks sync.Map
	sendMail            func(context.Context, notifications.MailConfig, notifications.MailMessage) error
	vpnMutex            sync.Mutex
	vpnFlows            map[string]*browserVPNFlow
	vpnFactory          func(string) (VPNClient, error)
	pendingVPN          map[string]*browserVPNFlow
	recoveryMutex       sync.Mutex
	recoveries          map[string]*authRecoveryState
	gatewayRequired     bool
	adminAccount        string
	siteLoginWindows    map[string]loginWindow
	siteAuthSlots       chan struct{}
	trustedProxies      []netip.Prefix
}

func NewAPI(database *store.Store, accounts *Accounts, version string) *API {
	api := NewInactiveAPI(database, accounts, version)
	api.StartBackgroundWork()
	return api
}

func NewInactiveAPI(database *store.Store, accounts *Accounts, version string) *API {
	api := &API{database: database, accounts: accounts, version: version, loginWindows: map[string]loginWindow{}, resources: NewResources(database, accounts)}
	api.sendMail = notifications.SendMail
	api.vpnFlows = map[string]*browserVPNFlow{}
	api.pendingVPN = map[string]*browserVPNFlow{}
	api.recoveries = map[string]*authRecoveryState{}
	api.gatewayRequired = accounts.mode != "local-single"
	api.siteLoginWindows = map[string]loginWindow{}
	api.siteAuthSlots = make(chan struct{}, 2)
	api.vpnFactory = func(account string) (VPNClient, error) { return campus.NewLoginClient(account) }
	accounts.recoverVPNService = api.recoverVPNService
	api.registerAcademicResources()
	api.registerOutlineResource()
	api.registerMessagesResource()
	api.registerExperimentResource()
	api.registerResearchResource()
	api.registerFestivalResource()
	api.registerTimetableResources()
	api.registerUserResource()
	accounts.onReady = api.resumeAccountWork
	return api
}

func (api *API) StartBackgroundWork() {
	api.startAutomationScheduler()
	api.startMailSender()
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json; charset=utf-8")
	response.Header().Set("Cache-Control", "no-store")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.WriteHeader(status)
	json.NewEncoder(response).Encode(value)
}

func decodeJSON(response http.ResponseWriter, request *http.Request, target any) bool {
	decoder := json.NewDecoder(http.MaxBytesReader(response, request.Body, 4*1024*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		writeJSON(response, 422, map[string]string{"detail": "请求内容无效"})
		return false
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		writeJSON(response, 422, map[string]string{"detail": "请求只能包含一个JSON对象"})
		return false
	}
	return true
}

func (api *API) Register(router *http.ServeMux) {
	api.registerLogs(router)
	api.registerRemoteRecovery(router)
	api.registerGPA(router)
	api.registerCache(router)
	api.registerAppearance(router)
	api.registerScores(router)
	api.registerReport(router)
	api.registerOffline(router)
	api.registerOutlineMetadata(router)
	api.registerOutlineQuery(router)
	api.registerMessages(router)
	api.registerExams(router)
	api.registerExperiment(router)
	api.registerResearch(router)
	api.registerAcademicDocuments(router)
	api.registerFestival(router)
	api.registerTimetable(router)
	api.registerPersonalTimetable(router)
	api.registerAgenda(router)
	api.registerUser(router)
	api.registerEvaluation(router)
	api.registerJWXK(router)
	api.registerVPN(router)
	api.registerSiteAccess(router)
	router.HandleFunc("GET /api/system-settings/mail", api.mailSettings)
	router.HandleFunc("PUT /api/system-settings/mail", api.mailSettings)
	router.HandleFunc("POST /api/system-settings/mail/test", api.testMail)
	router.HandleFunc("GET /api/system-settings/auth-recovery", api.recoverySettings)
	router.HandleFunc("PUT /api/system-settings/auth-recovery", api.recoverySettings)
	router.HandleFunc("GET /api/grade-tracking/config", api.gradeTrackingConfig)
	router.HandleFunc("PUT /api/grade-tracking/config", api.gradeTrackingConfig)
	router.HandleFunc("GET /api/grade-tracking/status", api.gradeTrackingStatus)
	router.HandleFunc("POST /api/grade-tracking/check", api.gradeTrackingCheck)
	router.HandleFunc("PATCH /api/grade-tracking/enabled", api.gradeTrackingEnabled)
	router.HandleFunc("GET /api/health", api.health)
	router.HandleFunc("GET /api/status", api.status)
	router.HandleFunc("GET /api/client/bootstrap", api.bootstrap)
	router.HandleFunc("GET /api/client/updates", api.updates)
	router.HandleFunc("GET /api/runtime/update", api.runtimeUpdate)
	router.HandleFunc("POST /api/schedule/conflicts/check", api.scheduleConflicts)
	router.HandleFunc("GET /api/course-selection/official-withdrawal", api.officialWithdrawals)
	router.HandleFunc("POST /api/course-selection/official-withdrawal/deselect", api.officialDeselect)
	router.HandleFunc("POST /api/runtime/shutdown", api.shutdownDesktop)
	router.HandleFunc("POST /api/runtime/update/download", api.runtimeDownload)
	router.HandleFunc("POST /api/runtime/update/{job_id}/install", api.installDesktopUpdate)
	router.HandleFunc("GET /api/runtime/update/{job_id}", api.runtimeDownloadJob)
	router.HandleFunc("GET /api/auth/pending", api.vpnPending)
	router.HandleFunc("POST /api/login", api.login)
	router.HandleFunc("POST /api/logout", api.logout)
	router.HandleFunc("GET /api/documents/{namespace}/{key}", api.documentRead)
	router.HandleFunc("PUT /api/documents/{namespace}/{key}", api.documentSave)
	router.HandleFunc("DELETE /api/documents/{namespace}/{key}", api.documentDelete)
}

func (api *API) Boundary(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if api.accounts.mode == "local-single" {
			host := request.Host
			if parsed, _, err := net.SplitHostPort(host); err == nil {
				host = parsed
			}
			address := net.ParseIP(host)
			if host != "localhost" && (address == nil || !address.IsLoopback()) {
				writeJSON(response, 403, map[string]string{"detail": "本机模式仅接受回环地址访问"})
				return
			}
		}
		if request.Method != "GET" && request.Method != "HEAD" && request.Method != "OPTIONS" {
			origin := request.Header.Get("Origin")
			if origin != "" {
				parsed, err := url.Parse(origin)
				if err != nil || parsed.Host != request.Host || (parsed.Scheme != "https" && parsed.Scheme != "http") {
					writeJSON(response, 403, map[string]string{"detail": "拒绝跨站写入请求"})
					return
				}
			}
			if request.Header.Get("Sec-Fetch-Site") == "cross-site" {
				writeJSON(response, 403, map[string]string{"detail": "拒绝跨站写入请求"})
				return
			}
			if request.Body != nil && request.ContentLength != 0 && !strings.HasPrefix(strings.ToLower(request.Header.Get("Content-Type")), "application/json") {
				writeJSON(response, 415, map[string]string{"detail": "请使用JSON请求"})
				return
			}
		}
		if api.gatewayRequired && strings.HasPrefix(request.URL.Path, "/api/") && !publicSitePath(request.URL.Path) && !api.siteAuthenticated(request) {
			writeJSON(response, 401, map[string]string{"detail": "需要先验证服务器访问密码", "code": "ACCESS_REQUIRED", "error_code": "ACCESS_REQUIRED"})
			return
		}
		api.observeRequest(response, request, next)
	})
}

func (api *API) runtimeInfo() map[string]any {
	profile := "desktop"
	if api.accounts.mode != "local-single" {
		profile = "server"
	}
	return map[string]any{"status": "ok", "version": api.version, "backend": "go", "profile": profile, "deployment_mode": api.accounts.mode, "mobile_api_version": 1, "migration_in_progress": true,
		"capabilities": map[string]bool{"native_notifications": false, "remote_auth_recovery": true, "system_mail": true, "mobile_local_backend": false}}
}

func (api *API) health(response http.ResponseWriter, request *http.Request) {
	info := api.runtimeInfo()
	if api.accounts.mode == "local-single" && api.desktopShutdown != nil && request.Host == api.desktopHost {
		info["shutdown_token"] = api.desktopToken
	}
	response.Header().Set("Cache-Control", "no-store")
	writeJSON(response, 200, info)
}

func (api *API) session(request *http.Request) (store.Session, error) {
	cookie, err := request.Cookie(sessionCookie)
	if err != nil {
		return store.Session{}, store.ErrAccess
	}
	return api.database.Session(request.Context(), cookie.Value)
}

func (api *API) requireSession(response http.ResponseWriter, request *http.Request) (store.Session, bool) {
	if session, ok := request.Context().Value(offlineIdentityKey{}).(store.Session); ok {
		return session, true
	}
	session, err := api.session(request)
	if err != nil {
		writeJSON(response, 401, map[string]string{"detail": "请先登录", "error_code": "AUTH_REQUIRED"})
		return store.Session{}, false
	}
	return session, true
}

func (api *API) authSnapshot(request *http.Request) map[string]any {
	result := map[string]any{"is_logged_in": false, "current_user": nil, "network_mode": "direct", "identity_epoch": 0, "has_credentials": false, "has_local_data": false, "recovery": map[string]any{"status": "idle"}, "pending_auth": nil}
	result["pending_auth"] = api.vpnPendingSnapshot(request)
	if session, err := api.session(request); err == nil {
		result["current_user"], result["identity_epoch"] = session.Account, session.Generation
		result["recovery"] = api.authRecoverySnapshot(session)
		if payload, err := api.database.ReadSecret(request.Context(), session.Account, "campus-session"); err == nil {
			var saved savedCampusSession
			result["has_credentials"] = json.Unmarshal(payload, &saved) == nil && saved.Account == session.Account && saved.Password != ""
		}
		if runtime := api.accounts.Runtime(session); runtime != nil {
			loggedIn, mode := runtime.Client.Status()
			result["is_logged_in"], result["network_mode"] = loggedIn, mode
		}
	}
	if session, err := api.offlineIdentity(request); err == nil {
		for resource, spec := range api.resources.specs {
			if spec.Offline {
				if _, err := api.database.ReadCache(request.Context(), session, resource, "default"); err == nil {
					result["has_local_data"] = true
					break
				}
			}
		}
	}
	return result
}

func (api *API) status(response http.ResponseWriter, request *http.Request) {
	writeJSON(response, 200, api.authSnapshot(request))
}

func (api *API) bootstrap(response http.ResponseWriter, request *http.Request) {
	var cursor int64
	var timetable any
	var avatar any
	if session, err := api.session(request); err == nil {
		_, cursor, _ = api.database.CacheEvents(request.Context(), session, nil, 1)
		timetable = api.timetableSnapshot(request.Context(), session)
		avatar = api.avatarSnapshot(request.Context(), session)
	}
	writeJSON(response, 200, map[string]any{"schema_version": 1, "runtime": api.runtimeInfo(), "auth": api.authSnapshot(request), "pending_auth": api.vpnPendingSnapshot(request), "cache_cursor": cursor, "timetable": timetable, "avatar": avatar})
}

func (api *API) updates(response http.ResponseWriter, request *http.Request) {
	cache := map[string]any{"events": []any{}, "cursor": 0}
	if session, err := api.session(request); err == nil {
		if snapshot, err := api.eventSnapshot(request, session, "cursor"); err == nil {
			cache = snapshot
		} else {
			api.resourceError(response, err)
			return
		}
	}
	writeJSON(response, 200, map[string]any{"schema_version": 1, "auth": api.authSnapshot(request), "pending_auth": api.vpnPendingSnapshot(request), "cache": cache})
}

func (api *API) admitLogin(request *http.Request) bool {
	address := api.clientSource(request)
	api.limitMutex.Lock()
	defer api.limitMutex.Unlock()
	now := time.Now()
	for source, window := range api.loginWindows {
		if now.Sub(window.start) >= time.Minute {
			delete(api.loginWindows, source)
		}
	}
	window, exists := api.loginWindows[address]
	if !exists {
		if len(api.loginWindows) >= 2048 {
			return false
		}
		window.start = now
	}
	window.attempts++
	api.loginWindows[address] = window
	return window.attempts <= 10
}

func (api *API) login(response http.ResponseWriter, request *http.Request) {
	if !api.admitLogin(request) {
		response.Header().Set("Retry-After", "60")
		writeJSON(response, 429, map[string]string{"detail": "登录尝试过于频繁，请稍后重试"})
		return
	}
	var input struct {
		Username string `json:"username"`
		Password string `json:"password"`
		Remember bool   `json:"remember"`
		Mode     string `json:"network_mode"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Mode != "" && input.Mode != "direct" {
		writeJSON(response, 409, map[string]any{"success": false, "requires_webvpn": true, "error_code": "WEBVPN_REQUIRED", "message": "请使用WebVPN专用登录入口"})
		return
	}
	account, err := campus.NormalizeAccount(input.Username)
	if err != nil {
		writeJSON(response, 422, map[string]string{"detail": err.Error()})
		return
	}
	api.cancelBrowserVPN(request)
	if err := api.accounts.Login(request.Context(), account, input.Password, input.Remember); err != nil {
		var failure *campus.Error
		if errors.As(err, &failure) {
			writeJSON(response, 200, map[string]any{"success": false, "message": failure.Message, "error_code": failure.Code, "requires_webvpn": failure.Code == "WEBVPN_REQUIRED"})
			return
		}
		if errors.Is(err, store.ErrAccess) {
			writeJSON(response, 403, map[string]string{"detail": "账号未获准访问或活动已停止"})
			return
		}
		writeJSON(response, 500, map[string]string{"detail": "无法保存登录会话"})
		return
	}
	lifetime := 12 * time.Hour
	if input.Remember {
		lifetime = 30 * 24 * time.Hour
	}
	token, err := api.accounts.CreateSession(request.Context(), account, lifetime)
	if err != nil {
		writeJSON(response, 500, map[string]string{"detail": "无法创建访问会话"})
		return
	}
	http.SetCookie(response, &http.Cookie{Name: sessionCookie, Value: token, Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: int(lifetime.Seconds())})
	if session, err := api.database.Session(request.Context(), token); err == nil {
		api.resumeAccountWork(session)
	}
	writeJSON(response, 200, map[string]any{"success": true, "message": "登录成功", "username": account, "network_mode": "direct"})
}

func (api *API) logout(response http.ResponseWriter, request *http.Request) {
	clearData := false
	if values, exists := request.URL.Query()["clear_data"]; exists {
		if len(values) != 1 || (values[0] != "true" && values[0] != "false") {
			writeJSON(response, 422, map[string]string{"detail": "clear_data 必须是 true 或 false"})
			return
		}
		clearData = values[0] == "true"
	}
	cleared := false
	adopted, hasAdopted := api.cancelBrowserVPN(request)
	if session, err := api.session(request); err == nil {
		if err := api.accounts.stopSession(request.Context(), session, true, clearData); err != nil {
			writeJSON(response, 500, map[string]string{"detail": "停止账号活动失败，请重试"})
			return
		}
		if session.Account == adopted.Account {
			hasAdopted = false
		}
		cleared = clearData
	}
	if hasAdopted {
		if err := api.accounts.Stop(request.Context(), adopted.Account, true); err != nil {
			writeJSON(response, 500, map[string]string{"detail": "停止新登录会话失败，请重试"})
			return
		}
	}
	http.SetCookie(response, &http.Cookie{Name: sessionCookie, Value: "", Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: -1})
	writeJSON(response, 200, map[string]any{"success": true, "message": "已退出登录并停止账号活动", "data_cleared": cleared})
}

func (api *API) documentRead(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	document, err := api.database.Read(request.Context(), session.Account, request.PathValue("namespace"), request.PathValue("key"))
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, document)
}

func (api *API) documentSave(response http.ResponseWriter, request *http.Request) {
	api.writeDocument(response, request, false)
}
func (api *API) documentDelete(response http.ResponseWriter, request *http.Request) {
	api.writeDocument(response, request, true)
}

func (api *API) writeDocument(response http.ResponseWriter, request *http.Request, deleted bool) {
	if request.PathValue("namespace") == "settings" && request.PathValue("key") == "legacy-mail-import" {
		writeJSON(response, 403, map[string]string{"detail": "迁移记录只能由维护命令创建"})
		return
	}
	if request.PathValue("namespace") == "theme" && request.PathValue("key") == "appearance" {
		writeJSON(response, 403, map[string]string{"detail": "外观配置请使用专用接口"})
		return
	}
	if request.PathValue("namespace") == "settings" && request.PathValue("key") == "cache" {
		writeJSON(response, 403, map[string]string{"detail": "缓存策略请使用专用设置接口"})
		return
	}
	if request.PathValue("namespace") == gradeTrackingNamespace || request.PathValue("namespace") == "auth-recovery" || request.PathValue("namespace") == "experiment-operations" || request.PathValue("namespace") == "research-favorites" || request.PathValue("namespace") == "research-operations" {
		writeJSON(response, 403, map[string]string{"detail": "此配置和状态请使用专用接口"})
		return
	}
	if request.PathValue("namespace") == jwxkAutomationSettingsNamespace && !deleted {
		writeJSON(response, 403, map[string]string{"detail": "自动化配置请使用专用设置接口保存"})
		return
	}
	if request.PathValue("namespace") == "selection-attempts" || request.PathValue("namespace") == jwxkWeightApplicationNamespace || request.PathValue("namespace") == jwxkAutomationTaskNamespace {
		writeJSON(response, 403, map[string]string{"detail": "提交记录只能由官方结果核验更新"})
		return
	}
	if (request.PathValue("namespace") == jwxkQueryNamespace || request.PathValue("namespace") == jwxkRoundNamespace || request.PathValue("namespace") == jwxkArchiveNamespace) && !deleted {
		writeJSON(response, 403, map[string]string{"detail": "官方目录快照仅由学校查询更新"})
		return
	}
	if request.PathValue("namespace") == "agenda" {
		writeJSON(response, 403, map[string]string{"detail": "日程请使用专用接口保存"})
		return
	}
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Data     json.RawMessage `json:"data"`
		Revision int64           `json:"revision"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if deleted {
		input.Data = json.RawMessage("null")
	}
	document, err := api.database.SaveForSession(request.Context(), session, request.PathValue("namespace"), request.PathValue("key"), input.Data, input.Revision, deleted)
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, document)
}

func (api *API) documentError(response http.ResponseWriter, err error) {
	status, message := 500, "无法保存或读取文档"
	switch {
	case errors.Is(err, store.ErrConflict):
		status, message = 409, "文档已被其他设备更新，请保留当前草稿并读取最新版本"
	case errors.Is(err, store.ErrMissing):
		status, message = 404, "文档不存在"
	case errors.Is(err, store.ErrAccess):
		status, message = 403, "账号已停用"
	}
	writeJSON(response, status, map[string]string{"detail": message})
}
