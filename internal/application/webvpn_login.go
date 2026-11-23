package application

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const vpnOwnerCookie = "neu_toolbox_login_flow"

type VPNClient interface {
	CampusClient
	StartVPNQR(context.Context) (map[string]any, error)
	PollVPNQR(context.Context, string) (map[string]any, error)
	StartVPNPassword(context.Context, string) (map[string]any, error)
	RefreshVPNCaptcha(context.Context, string) (map[string]any, error)
	SendVPNSMS(context.Context, string, string) (map[string]any, error)
	VerifyVPNSMS(context.Context, string, string) (map[string]any, error)
}

type browserVPNFlow struct {
	registeredAccount            string
	mutex                        sync.Mutex
	client                       VPNClient
	context                      context.Context
	cancel                       context.CancelFunc
	created                      time.Time
	identifier, password, target string
	remember                     bool
	adopted                      bool
	original                     *Runtime
	bound                        store.Session
	state                        map[string]any
	recoveryToken                string
}

func (api *API) registerVPN(router *http.ServeMux) {
	router.HandleFunc("POST /api/webvpn/qr/start", api.vpnStart)
	router.HandleFunc("POST /api/webvpn/password/start", api.vpnStart)
	for _, path := range []string{"qr/status", "qr/cancel", "sms/captcha/refresh", "sms/send", "sms/verify", "sms/cancel"} {
		router.HandleFunc("POST /api/webvpn/"+path, api.vpnAction)
	}
}

func vpnOwner(request *http.Request) string {
	cookie, err := request.Cookie(vpnOwnerCookie)
	if err != nil || len(cookie.Value) != 64 {
		return ""
	}
	if _, err := hex.DecodeString(cookie.Value); err != nil {
		return ""
	}
	return cookie.Value
}

func (api *API) cancelBrowserVPN(request *http.Request) (store.Session, bool) {
	flow := api.requestVPNFlow(request)
	return api.cancelVPNFlow(flow)
}

func (api *API) cancelVPNFlow(flow *browserVPNFlow) (store.Session, bool) {
	api.vpnMutex.Lock()
	for owner, candidate := range api.vpnFlows {
		if candidate == flow {
			delete(api.vpnFlows, owner)
		}
	}
	for account, candidate := range api.pendingVPN {
		if candidate == flow {
			delete(api.pendingVPN, account)
		}
	}
	api.vpnMutex.Unlock()
	if flow != nil {
		flow.cancel()
		flow.mutex.Lock()
		defer flow.mutex.Unlock()
		flow.password = ""
		if flow.adopted {
			return flow.bound, true
		}
		flow.client.Close()
		api.endPendingRecovery(flow)
	}
	return store.Session{}, false
}

func (api *API) requestVPNFlow(request *http.Request) *browserVPNFlow {
	api.vpnMutex.Lock()
	flow := api.vpnFlows[vpnOwner(request)]
	api.vpnMutex.Unlock()
	if flow != nil {
		return flow
	}
	session, err := api.session(request)
	if err != nil {
		return nil
	}
	api.vpnMutex.Lock()
	flow = api.pendingVPN[session.Account]
	api.vpnMutex.Unlock()
	if flow == nil || flow.bound.Generation != session.Generation || flow.bound.Account != session.Account || (flow.original != nil && api.accounts.Runtime(session) != flow.original) {
		return nil
	}
	return flow
}

func vpnResult(response http.ResponseWriter, result map[string]any, err error) {
	if result == nil {
		result = map[string]any{}
	}
	result["success"] = err == nil
	if err != nil {
		result["error_code"], result["message"] = "WEBVPN_UNKNOWN_ERROR", "登录未完成，请稍后重试"
		if result["status"] == nil {
			result["status"] = "error"
		}
		var failure *campus.Error
		if errors.As(err, &failure) {
			result["error_code"], result["message"] = failure.Code, failure.Message
		}
		if errors.Is(err, store.ErrAccess) {
			result["error_code"], result["message"] = "WEBVPN_ACCESS_DENIED", "账号未获准访问或此登录流程已停止"
		}
	}
	writeJSON(response, 200, result)
}

func (api *API) vpnStart(response http.ResponseWriter, request *http.Request) {
	if !api.admitLogin(request) {
		writeJSON(response, 429, map[string]string{"detail": "登录尝试过于频繁，请稍后重试"})
		return
	}
	var input struct {
		Username string `json:"username"`
		Password string `json:"password"`
		Remember bool   `json:"remember"`
		Target   string `json:"target_service"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Target == "" {
		input.Target = "primary"
	}
	if input.Target != "primary" && input.Target != "jwxk" && input.Target != "cxcy" {
		writeJSON(response, 422, map[string]string{"detail": "认证目标无效"})
		return
	}
	passwordFlow := strings.Contains(request.URL.Path, "/password/")
	if passwordFlow || strings.TrimSpace(input.Username) != "" {
		normalized, err := campus.NormalizeAccount(input.Username)
		if err != nil {
			vpnResult(response, nil, err)
			return
		}
		input.Username = normalized
	}
	var bound store.Session
	if session, err := api.session(request); err == nil {
		bound = session
	}
	if input.Target != "primary" {
		if bound.Account == "" {
			writeJSON(response, 401, map[string]string{"detail": "请先登录工具箱"})
			return
		}
		if input.Username != "" && input.Username != bound.Account {
			vpnResult(response, nil, store.ErrAccess)
			return
		}
		input.Username = bound.Account
	}
	if input.Username != "" {
		if api.accounts.mode == "local-single" {
			if err := api.database.AddAccount(request.Context(), input.Username); err != nil {
				vpnResult(response, nil, err)
				return
			}
		}
		generation, err := api.database.Generation(request.Context(), input.Username)
		if err != nil {
			vpnResult(response, nil, store.ErrAccess)
			return
		}
		bound = store.Session{Account: input.Username, Generation: generation}
	}
	if session, err := api.session(request); err == nil && session.Account == bound.Account && session.Generation == bound.Generation {
		api.cleanupPendingRecovery(session, api.accounts.Runtime(session))
	}
	api.vpnMutex.Lock()
	previousBrowser := api.vpnFlows[vpnOwner(request)]
	pending := api.pendingVPN[bound.Account]
	api.vpnMutex.Unlock()
	if pending != nil && pending.context.Err() == nil && time.Since(pending.created) < 15*time.Minute {
		vpnResult(response, nil, &campus.Error{Code: "SERVICE_INTERACTION_REQUIRED", Message: "请先继续或取消已有验证"})
		return
	}
	client, err := api.vpnFactory(input.Username)
	if err != nil {
		vpnResult(response, nil, err)
		return
	}
	if selector, ok := client.(interface{ SetVPNTarget(string) error }); ok {
		if err := selector.SetVPNTarget(input.Target); err != nil {
			client.Close()
			vpnResult(response, nil, err)
			return
		}
	}
	if previousBrowser != nil {
		api.cancelVPNFlow(previousBrowser)
	}
	owner := vpnOwner(request)
	if owner == "" {
		identifier := make([]byte, 32)
		if _, err := rand.Read(identifier); err != nil {
			client.Close()
			vpnResult(response, nil, err)
			return
		}
		owner = hex.EncodeToString(identifier)
	}
	ctx, cancel := context.WithCancel(api.accounts.context)
	flow := &browserVPNFlow{client: client, context: ctx, cancel: cancel, created: time.Now(), password: input.Password, remember: input.Remember, bound: bound, target: input.Target}
	if !passwordFlow {
		flow.remember = true
	}
	flow.mutex.Lock()
	defer flow.mutex.Unlock()
	api.vpnMutex.Lock()
	if pending := api.pendingVPN[bound.Account]; pending != nil && pending.context.Err() == nil && time.Since(pending.created) < 15*time.Minute {
		api.vpnMutex.Unlock()
		cancel()
		client.Close()
		vpnResult(response, nil, &campus.Error{Code: "SERVICE_INTERACTION_REQUIRED", Message: "请先继续或取消已有验证"})
		return
	}
	for key, existing := range api.vpnFlows {
		if time.Since(existing.created) > 15*time.Minute || existing.context.Err() != nil {
			existing.cancel()
			delete(api.vpnFlows, key)
		} else if bound.Account != "" && existing.registeredAccount == bound.Account && existing != previousBrowser {
			api.vpnMutex.Unlock()
			cancel()
			client.Close()
			vpnResult(response, nil, &campus.Error{Code: "SERVICE_INTERACTION_REQUIRED", Message: "请先继续或取消已有验证"})
			return
		}
	}
	if len(api.vpnFlows) >= 128 {
		api.vpnMutex.Unlock()
		cancel()
		client.Close()
		writeJSON(response, 429, map[string]string{"detail": "登录流程繁忙，请稍后重试"})
		return
	}
	if previous := api.vpnFlows[owner]; previous != nil {
		previous.cancel()
	}
	flow.registeredAccount = bound.Account
	api.vpnFlows[owner] = flow
	api.vpnMutex.Unlock()
	http.SetCookie(response, &http.Cookie{Name: vpnOwnerCookie, Value: owner, Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteStrictMode, MaxAge: 900})
	callContext, callCancel := context.WithCancel(flow.context)
	defer callCancel()
	stop := context.AfterFunc(request.Context(), callCancel)
	defer stop()
	var result map[string]any
	if passwordFlow {
		result, err = client.StartVPNPassword(callContext, input.Password)
	} else {
		result, err = client.StartVPNQR(callContext)
	}
	api.finishVPNResponse(response, request, flow, result, err)
}

func (api *API) vpnAction(response http.ResponseWriter, request *http.Request) {
	var input struct {
		Flow    string `json:"flow_id"`
		Code    string `json:"code"`
		Captcha string `json:"captcha_code"`
		Trust   bool   `json:"trust_device"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	flow := api.requestVPNFlow(request)
	if flow == nil {
		vpnResult(response, nil, &campus.Error{Code: "WEBVPN_FLOW_MISSING", Message: "登录流程不存在，请重新开始"})
		return
	}
	flow.mutex.Lock()
	defer flow.mutex.Unlock()
	if flow.context.Err() != nil || input.Flow == "" || flow.identifier != input.Flow {
		vpnResult(response, nil, &campus.Error{Code: "WEBVPN_FLOW_REPLACED", Message: "登录流程已被替换，请重新开始"})
		return
	}
	if !api.serviceFlowMatchesSession(request, flow) {
		vpnResult(response, nil, store.ErrAccess)
		return
	}
	if time.Since(flow.created) > 15*time.Minute {
		flow.cancel()
		flow.client.Close()
		flow.password = ""
		vpnResult(response, nil, &campus.Error{Code: "WEBVPN_FLOW_EXPIRED", Message: "登录流程已过期，请重新开始"})
		return
	}
	if strings.HasSuffix(request.URL.Path, "/cancel") {
		flow.cancel()
		flow.client.Close()
		flow.password = ""
		api.endPendingRecovery(flow)
		vpnResult(response, map[string]any{"status": "cancelled"}, nil)
		return
	}
	if flow.bound.Account != "" {
		generation, err := api.database.Generation(request.Context(), flow.bound.Account)
		if err != nil || generation != flow.bound.Generation {
			flow.cancel()
			flow.client.Close()
			flow.password = ""
			vpnResult(response, nil, store.ErrAccess)
			return
		}
	}
	ctx, cancel := context.WithCancel(flow.context)
	defer cancel()
	stop := context.AfterFunc(request.Context(), cancel)
	defer stop()
	var result map[string]any
	var err error
	switch {
	case strings.HasSuffix(request.URL.Path, "/qr/status"):
		result, err = flow.client.PollVPNQR(ctx, input.Flow)
	case strings.HasSuffix(request.URL.Path, "/captcha/refresh"):
		result, err = flow.client.RefreshVPNCaptcha(ctx, input.Flow)
	case strings.HasSuffix(request.URL.Path, "/sms/send"):
		result, err = flow.client.SendVPNSMS(ctx, input.Flow, input.Captcha)
	case strings.HasSuffix(request.URL.Path, "/sms/verify"):
		result, err = flow.client.VerifyVPNSMS(ctx, input.Flow, input.Code)
	}
	api.finishVPNResponse(response, request, flow, result, err)
}

func (api *API) finishVPNResponse(response http.ResponseWriter, request *http.Request, flow *browserVPNFlow, result map[string]any, err error) {
	if result != nil {
		if identifier, ok := result["flow_id"].(string); ok {
			flow.identifier = identifier
		}
		if flow.state == nil {
			flow.state = map[string]any{}
		}
		for _, key := range []string{"status", "flow_id", "expires_in", "captcha_image", "sms_verified", "error_code", "message"} {
			if value, ok := result[key]; ok {
				flow.state[key] = value
			}
		}
		if seconds, ok := result["expires_in"].(int); ok {
			flow.state["expires_at"] = time.Now().Add(time.Duration(seconds) * time.Second).Unix()
		}
		if result["status"] == "captcha_refreshed" || result["status"] == "sent" {
			flow.state["status"] = "sms_required"
		}
		if result["sms_verified"] == true {
			delete(flow.state, "captcha_image")
		}
	}
	if err == nil && result["status"] == "authenticated" {
		if flow.context.Err() != nil {
			vpnResult(response, nil, store.ErrAccess)
			return
		}
		if err := api.adoptVPN(request.Context(), flow); err != nil {
			flow.client.Close()
			flow.cancel()
			flow.password = ""
			vpnResult(response, nil, err)
			return
		}
		if flow.target == "primary" {
			flow.adopted = true
			generation, _ := api.database.Generation(request.Context(), flow.client.Account())
			flow.bound = store.Session{Account: flow.client.Account(), Generation: generation}
			if flow.context.Err() != nil {
				vpnResult(response, nil, store.ErrAccess)
				return
			}
			lifetime := 12 * time.Hour
			if flow.remember {
				lifetime = 30 * 24 * time.Hour
			}
			token, err := api.accounts.CreateSession(request.Context(), flow.client.Account(), lifetime)
			if err != nil {
				vpnResult(response, nil, err)
				return
			}
			http.SetCookie(response, &http.Cookie{Name: sessionCookie, Value: token, Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: int(lifetime.Seconds())})
			if session, err := api.database.Session(request.Context(), token); err == nil {
				api.resumeAccountWork(session)
			}
		}
		result["target_service"] = flow.target
		flow.password = ""
		flow.cancel()
		api.vpnMutex.Lock()
		for key, candidate := range api.vpnFlows {
			if candidate == flow {
				delete(api.vpnFlows, key)
			}
		}
		for account, candidate := range api.pendingVPN {
			if candidate == flow {
				delete(api.pendingVPN, account)
			}
		}
		api.vpnMutex.Unlock()
	}
	vpnResult(response, result, err)
}

func (api *API) adoptVPN(ctx context.Context, flow *browserVPNFlow) error {
	api.vpnMutex.Lock()
	defer api.vpnMutex.Unlock()
	current := false
	for _, candidate := range api.vpnFlows {
		if candidate == flow {
			current = true
			break
		}
	}
	for _, candidate := range api.pendingVPN {
		if candidate == flow {
			current = true
			break
		}
	}
	if !current || flow.context.Err() != nil {
		return store.ErrAccess
	}
	account := flow.client.Account()
	if loggedIn, _ := flow.client.Status(); !loggedIn || account == "" {
		return store.ErrAccess
	}
	if flow.bound.Account != "" && flow.bound.Account != account {
		return store.ErrAccess
	}
	if api.accounts.mode == "local-single" {
		if err := api.database.AddAccount(ctx, account); err != nil {
			return err
		}
	}
	generation, err := api.database.Generation(ctx, account)
	if err != nil || (flow.bound.Account != "" && generation != flow.bound.Generation) {
		return store.ErrAccess
	}
	lockValue, _ := api.accounts.loginLocks.LoadOrStore(account, &sync.Mutex{})
	lock := lockValue.(*sync.Mutex)
	lock.Lock()
	defer lock.Unlock()
	if (flow.original != nil || flow.recoveryToken != "") && api.accounts.Runtime(flow.bound) != flow.original {
		return store.ErrAccess
	}
	if flow.recoveryToken != "" {
		if err := api.database.FinishRecoveryContext(ctx, flow.recoveryToken, "consumed"); err != nil {
			return err
		}
	}
	if flow.target != "primary" {
		return api.adoptVPNService(ctx, flow, store.Session{Account: account, Generation: generation})
	}
	password := flow.password
	if password == "" && flow.remember {
		if payload, err := api.database.ReadSecret(ctx, account, "campus-session"); err == nil {
			var saved struct {
				Account  string `json:"account"`
				Password string `json:"password"`
			}
			if json.Unmarshal(payload, &saved) == nil && saved.Account == account {
				password = saved.Password
			}
		}
	}
	return api.accounts.installClient(ctx, flow.client, password, flow.remember, generation)
}

func (api *API) vpnPending(response http.ResponseWriter, request *http.Request) {
	writeJSON(response, 200, api.vpnPendingSnapshot(request))
}

func (api *API) vpnPendingSnapshot(request *http.Request) map[string]any {
	return api.scopedVPNPendingSnapshot(request, store.Session{}, "")
}

func (api *API) serviceFlowMatchesSession(request *http.Request, flow *browserVPNFlow) bool {
	if flow.target != "jwxk" && flow.target != "cxcy" {
		return true
	}
	session, err := api.session(request)
	return err == nil && session.Account == flow.bound.Account && session.Generation == flow.bound.Generation
}

func (api *API) scopedVPNPendingSnapshot(request *http.Request, session store.Session, target string) map[string]any {
	flow := api.requestVPNFlow(request)
	if flow == nil {
		return map[string]any{"required": false}
	}
	if !flow.mutex.TryLock() {
		return map[string]any{"required": false}
	}
	defer flow.mutex.Unlock()
	if !api.serviceFlowMatchesSession(request, flow) {
		return map[string]any{"required": false}
	}
	if target != "" && (flow.target != target || flow.bound.Account != session.Account || flow.bound.Generation != session.Generation) {
		return map[string]any{"required": false}
	}
	if flow.context.Err() != nil || time.Since(flow.created) > 15*time.Minute || (flow.state["status"] != "sms_required" && flow.state["status"] != "session_pending") {
		return map[string]any{"required": false}
	}
	if expires, ok := flow.state["expires_at"].(int64); ok && time.Now().Unix() > expires+60 {
		return map[string]any{"required": false}
	}
	result := map[string]any{"required": true, "network_mode": "webvpn", "target_service": flow.target}
	for key, value := range flow.state {
		result[key] = value
	}
	return result
}
