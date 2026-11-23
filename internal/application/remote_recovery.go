package application

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func remoteRecoveryPath(path string) bool {
	parts := strings.Split(strings.TrimPrefix(path, "/api/auth-recovery/"), "/")
	if !strings.HasPrefix(path, "/api/auth-recovery/") || len(parts) < 2 || len(parts[0]) != 129 {
		return false
	}
	switch strings.Join(parts[1:], "/") {
	case "status", "start", "poll", "captcha/refresh", "sms/send", "sms/verify", "cancel":
		return true
	}
	return false
}

func (api *API) registerRemoteRecovery(router *http.ServeMux) {
	for _, action := range []string{"status", "poll"} {
		router.HandleFunc("GET /api/auth-recovery/{token}/"+action, api.remoteRecovery)
	}
	for _, action := range []string{"start", "captcha/refresh", "sms/send", "sms/verify", "cancel"} {
		router.HandleFunc("POST /api/auth-recovery/{token}/"+action, api.remoteRecovery)
	}
}

func (api *API) discardRemoteFlow(key string, flow *browserVPNFlow) {
	flow.cancel()
	flow.password, flow.recoveryToken = "", ""
	if !flow.adopted {
		flow.client.Close()
	}
	api.vpnMutex.Lock()
	if api.vpnFlows[key] == flow {
		delete(api.vpnFlows, key)
	}
	if api.pendingVPN[flow.bound.Account] == flow {
		delete(api.pendingVPN, flow.bound.Account)
	}
	api.vpnMutex.Unlock()
}

func recoveryFlowResult(flow *browserVPNFlow, recovery store.RecoveryContext) map[string]any {
	result := map[string]any{"success": true, "target_service": recovery.Target, "link_expires_at": recovery.Expires}
	for _, key := range []string{"status", "qr_content", "qr_url", "poll_interval", "captcha_image", "sms_verified", "message", "error_code", "expires_in"} {
		if value, ok := flow.state[key]; ok {
			result[key] = value
		}
	}
	if expiry, ok := flow.state["expires_at"].(int64); ok {
		result["expires_in"] = max(int64(0), min(expiry, recovery.Expires)-time.Now().Unix())
	}
	return result
}

func (api *API) remoteRecovery(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Referrer-Policy", "no-referrer")
	token := request.PathValue("token")
	recovery, err := api.database.RecoveryContext(request.Context(), token)
	if err != nil {
		writeJSON(response, 404, map[string]string{"detail": "恢复链接不存在、已过期或已经使用"})
		return
	}
	lockValue, _ := api.remoteRecoveryLocks.LoadOrStore(recovery.Account, &sync.Mutex{})
	lock := lockValue.(*sync.Mutex)
	lock.Lock()
	defer lock.Unlock()
	if _, err := api.database.RecoveryContext(request.Context(), token); err != nil {
		writeJSON(response, 404, map[string]string{"detail": "恢复链接已失效"})
		return
	}
	action := strings.TrimPrefix(request.URL.Path, "/api/auth-recovery/"+token+"/")
	session := store.Session{Account: recovery.Account, Generation: recovery.Generation}
	key := "recovery:" + recovery.ID
	api.vpnMutex.Lock()
	flow := api.vpnFlows[key]
	if flow == nil {
		pending := api.pendingVPN[session.Account]
		if pending != nil && pending.bound == session && pending.target == recovery.Target {
			flow = pending
		}
	}
	api.vpnMutex.Unlock()
	if flow != nil {
		flow.mutex.Lock()
		expiry, _ := flow.state["expires_at"].(int64)
		qrExpired := action == "start" && flow.state["status"] != "sms_required" && flow.state["status"] != "session_pending" && expiry > 0 && time.Now().Unix() >= expiry
		if flow.context.Err() != nil || qrExpired || time.Since(flow.created) > 15*time.Minute || flow.original != nil && api.accounts.Runtime(session) != flow.original {
			api.discardRemoteFlow(key, flow)
			flow.mutex.Unlock()
			flow = nil
		}
	}
	if flow == nil && action == "status" {
		writeJSON(response, 200, map[string]any{"status": "ready", "target_service": recovery.Target, "link_expires_at": recovery.Expires})
		return
	}
	if action == "cancel" {
		if flow != nil {
			api.discardRemoteFlow(key, flow)
			api.endPendingRecovery(flow)
			flow.mutex.Unlock()
		}
		if err := api.database.FinishRecoveryContext(request.Context(), token, "cancelled"); err != nil {
			writeJSON(response, 404, map[string]string{"detail": "恢复链接已失效"})
			return
		}
		writeJSON(response, 200, map[string]any{"success": true, "status": "cancelled"})
		return
	}
	created := false
	if flow == nil && action == "start" {
		if !api.admitLogin(request) {
			writeJSON(response, 429, map[string]string{"detail": "登录尝试过于频繁，请稍后重试"})
			return
		}
		client, err := api.vpnFactory(session.Account)
		if err != nil {
			writeJSON(response, 503, map[string]string{"detail": "暂时无法创建认证流程"})
			return
		}
		if selector, ok := client.(interface{ SetVPNTarget(string) error }); ok {
			if err := selector.SetVPNTarget(recovery.Target); err != nil {
				client.Close()
				writeJSON(response, 503, map[string]string{"detail": "认证目标暂不可用"})
				return
			}
		}
		deadline := time.Unix(recovery.Expires, 0)
		if maximum := time.Now().Add(15 * time.Minute); maximum.Before(deadline) {
			deadline = maximum
		}
		flowContext, cancel := context.WithDeadline(api.accounts.context, deadline)
		flow = &browserVPNFlow{client: client, context: flowContext, cancel: cancel, created: time.Now(), target: recovery.Target, remember: true, bound: session, original: api.accounts.Runtime(session), state: map[string]any{}}
		flow.mutex.Lock()
		api.vpnMutex.Lock()
		if len(api.vpnFlows) >= 128 {
			api.vpnMutex.Unlock()
			flow.mutex.Unlock()
			cancel()
			client.Close()
			writeJSON(response, 429, map[string]string{"detail": "认证流程繁忙，请稍后重试"})
			return
		}
		flow.registeredAccount = session.Account
		api.vpnFlows[key] = flow
		api.vpnMutex.Unlock()
		candidate := flow
		context.AfterFunc(flowContext, func() {
			candidate.mutex.Lock()
			defer candidate.mutex.Unlock()
			api.discardRemoteFlow(key, candidate)
		})
		created = true
	}
	if flow == nil {
		writeJSON(response, 409, map[string]string{"detail": "请先重新开始认证", "error_code": "WEBVPN_FLOW_MISSING"})
		return
	}
	defer flow.mutex.Unlock()
	if action == "status" || action == "start" && !created {
		writeJSON(response, 200, recoveryFlowResult(flow, recovery))
		return
	}
	ctx, cancel := context.WithDeadline(request.Context(), time.Unix(recovery.Expires, 0))
	defer cancel()
	stop := context.AfterFunc(flow.context, cancel)
	defer stop()
	release, err := api.resources.acquireAccount(ctx, session)
	if err != nil {
		writeJSON(response, 409, map[string]string{"detail": "认证请求已取消"})
		return
	}
	defer release()
	select {
	case api.resources.slots <- struct{}{}:
		defer func() { <-api.resources.slots }()
	case <-ctx.Done():
		writeJSON(response, 409, map[string]string{"detail": "认证请求已取消"})
		return
	}
	var result map[string]any
	var callError error
	switch action {
	case "start":
		result, callError = flow.client.StartVPNQR(ctx)
	case "poll":
		result, callError = flow.client.PollVPNQR(ctx, flow.identifier)
	case "captcha/refresh":
		result, callError = flow.client.RefreshVPNCaptcha(ctx, flow.identifier)
	case "sms/send", "sms/verify":
		var input struct {
			Captcha string `json:"captcha_code"`
			Code    string `json:"code"`
			Trust   bool   `json:"trust_device"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		if action == "sms/send" {
			result, callError = flow.client.SendVPNSMS(ctx, flow.identifier, input.Captcha)
		} else {
			result, callError = flow.client.VerifyVPNSMS(ctx, flow.identifier, input.Code)
		}
	}
	if _, err := api.database.RecoveryContext(ctx, token); err != nil || flow.context.Err() != nil {
		api.discardRemoteFlow(key, flow)
		writeJSON(response, 404, map[string]string{"detail": "恢复链接已失效，本次结果未采用"})
		return
	}
	if callError != nil {
		var captchaFailure *campus.Error
		if !created && errors.As(callError, &captchaFailure) && captchaFailure.Code == "WEBVPN_CAPTCHA_INVALID" && result != nil && result["status"] == "sms_required" {
			for _, name := range []string{"captcha_image", "expires_in"} {
				if value, exists := result[name]; exists {
					flow.state[name] = value
				}
			}
			if identifier, ok := result["flow_id"].(string); ok && identifier != "" {
				flow.identifier = identifier
			}
			if seconds, ok := result["expires_in"].(int); ok {
				flow.state["expires_at"] = time.Now().Add(time.Duration(seconds) * time.Second).Unix()
			}
			flow.state["status"] = "sms_required"
			output := recoveryFlowResult(flow, recovery)
			output["success"], output["status"], output["captcha_invalid"] = false, "captcha_invalid", true
			output["message"], output["error_code"] = captchaFailure.Message, captchaFailure.Code
			writeJSON(response, 200, output)
			return
		}
		if created {
			api.discardRemoteFlow(key, flow)
		}
		var failure *campus.Error
		code := "UPSTREAM_UNAVAILABLE"
		message := "学校认证暂时不可用，请稍后重试"
		if errors.As(callError, &failure) {
			code, message = failure.Code, failure.Message
		}
		writeJSON(response, 502, map[string]string{"detail": message, "error_code": code})
		return
	}
	if identifier, ok := result["flow_id"].(string); ok {
		flow.identifier = identifier
	}
	for name, value := range result {
		flow.state[name] = value
	}
	if seconds, ok := result["expires_in"].(int); ok {
		flow.state["expires_at"] = time.Now().Add(time.Duration(seconds) * time.Second).Unix()
	}
	if result["sms_verified"] == true {
		delete(flow.state, "captcha_image")
	}
	if result["status"] == "authenticated" {
		flow.recoveryToken = token
		err := api.adoptVPN(ctx, flow)
		flow.recoveryToken = ""
		if err != nil {
			api.discardRemoteFlow(key, flow)
			writeJSON(response, 409, map[string]string{"detail": "认证结果未采用，请返回工具箱核验当前登录"})
			return
		}
		flow.adopted = true
		api.discardRemoteFlow(key, flow)
		api.resumeAccountWork(session)
		writeJSON(response, 200, map[string]any{"success": true, "status": "authenticated", "target_service": recovery.Target})
		return
	}
	output := recoveryFlowResult(flow, recovery)
	if result["status"] == "sent" || result["status"] == "captcha_refreshed" {
		flow.state["status"] = "sms_required"
	}
	writeJSON(response, 200, output)
}
