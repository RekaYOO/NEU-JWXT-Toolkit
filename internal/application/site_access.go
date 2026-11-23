package application

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func siteCookie(purpose string) string {
	if purpose == "access" {
		return "neu_jwxt_access"
	}
	return "neu_toolbox_admin"
}
func publicSitePath(path string) bool {
	return path == "/api/health" || path == "/api/access/status" || path == "/api/access/login" || path == "/api/access/logout" || remoteRecoveryPath(path)
}

func (api *API) ConfigureRemoteAccess(ctx context.Context, accessPassword, adminPassword string) error {
	if api.accounts.mode == "local-single" {
		return nil
	}
	missing := map[string]string{}
	for _, item := range []struct{ purpose, password string }{{"access", accessPassword}, {"admin", adminPassword}} {
		configured, err := api.database.SitePasswordConfigured(ctx, item.purpose)
		if err != nil {
			return err
		}
		if configured {
			continue
		}
		if item.password == "" {
			return fmt.Errorf("initial %s password must be supplied through its environment variable", item.purpose)
		}
		missing[item.purpose] = item.password
	}
	if len(missing) != 0 {
		return api.database.InitializeSitePasswords(ctx, missing)
	}
	return nil
}

func (api *API) siteAuthenticated(request *http.Request, purpose string) bool {
	cookie, err := request.Cookie(siteCookie(purpose))
	return err == nil && api.database.SiteSession(request.Context(), purpose, cookie.Value)
}

func (api *API) registerSiteAccess(router *http.ServeMux) {
	router.HandleFunc("GET /api/access/status", api.accessStatus)
	router.HandleFunc("POST /api/access/login", func(response http.ResponseWriter, request *http.Request) { api.siteLogin(response, request, "access") })
	router.HandleFunc("POST /api/access/logout", func(response http.ResponseWriter, request *http.Request) { api.siteLogout(response, request, "access") })
	router.HandleFunc("GET /api/admin/status", api.adminStatus)
	router.HandleFunc("POST /api/admin/login", func(response http.ResponseWriter, request *http.Request) { api.siteLogin(response, request, "admin") })
	router.HandleFunc("POST /api/admin/logout", func(response http.ResponseWriter, request *http.Request) { api.siteLogout(response, request, "admin") })
	router.HandleFunc("GET /api/admin/accounts", api.adminAccounts)
	router.HandleFunc("POST /api/admin/accounts", api.adminAccounts)
	router.HandleFunc("POST /api/admin/accounts/{account}/{action}", api.adminAccountAction)
}

func (api *API) accessStatus(response http.ResponseWriter, request *http.Request) {
	configured, err := api.database.SitePasswordConfigured(request.Context(), "access")
	if err != nil {
		writeJSON(response, 503, map[string]string{"detail": "无法读取站点访问配置"})
		return
	}
	theme, err := api.siteTheme(request.Context())
	if err != nil {
		theme = "blue"
	}
	writeJSON(response, 200, map[string]any{"required": api.gatewayRequired, "configured": configured, "authenticated": !api.gatewayRequired || api.siteAuthenticated(request, "access"), "profile": api.runtimeInfo()["profile"], "site_default": theme})
}

func (api *API) adminStatus(response http.ResponseWriter, request *http.Request) {
	configured, err := api.database.SitePasswordConfigured(request.Context(), "admin")
	if err != nil {
		writeJSON(response, 503, map[string]string{"detail": "无法读取管理员配置"})
		return
	}
	writeJSON(response, 200, map[string]any{"configured": configured, "authenticated": api.siteAuthenticated(request, "admin"), "deployment_mode": api.accounts.mode})
}

func (api *API) siteLogin(response http.ResponseWriter, request *http.Request, purpose string) {
	if purpose == "access" && !api.gatewayRequired {
		writeJSON(response, 200, map[string]bool{"success": true})
		return
	}
	var input struct {
		Password string `json:"password"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	configured, err := api.database.SitePasswordConfigured(request.Context(), purpose)
	if err != nil || !configured {
		writeJSON(response, 503, map[string]string{"detail": "服务器尚未完成密码初始化"})
		return
	}
	address := api.clientSource(request)
	key := purpose + ":" + address
	api.limitMutex.Lock()
	for source, window := range api.siteLoginWindows {
		if time.Since(window.start) >= 5*time.Minute {
			delete(api.siteLoginWindows, source)
		}
	}
	window, exists := api.siteLoginWindows[key]
	if !exists {
		window.start = time.Now()
	}
	blocked := window.attempts >= 5 || !exists && len(api.siteLoginWindows) >= 4096
	if !blocked {
		window.attempts++
		api.siteLoginWindows[key] = window
	}
	api.limitMutex.Unlock()
	if blocked {
		response.Header().Set("Retry-After", "300")
		writeJSON(response, 429, map[string]string{"detail": "验证失败次数过多，请5分钟后重试"})
		return
	}
	select {
	case api.siteAuthSlots <- struct{}{}:
		defer func() { <-api.siteAuthSlots }()
	default:
		writeJSON(response, 429, map[string]string{"detail": "验证繁忙，请稍后重试"})
		return
	}
	lifetime := 7 * 24 * time.Hour
	if purpose == "admin" {
		lifetime = 8 * time.Hour
	}
	token, err := api.database.CreateSiteSession(request.Context(), purpose, input.Password, lifetime)
	if err != nil {
		if errors.Is(err, store.ErrAccess) {
			writeJSON(response, 401, map[string]string{"detail": "密码验证失败"})
		} else {
			writeJSON(response, 503, map[string]string{"detail": "无法创建访问会话"})
		}
		return
	}
	api.limitMutex.Lock()
	delete(api.siteLoginWindows, key)
	api.limitMutex.Unlock()
	http.SetCookie(response, &http.Cookie{Name: siteCookie(purpose), Value: token, Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: int(lifetime.Seconds())})
	writeJSON(response, 200, map[string]bool{"success": true})
}

func (api *API) siteLogout(response http.ResponseWriter, request *http.Request, purpose string) {
	purposes := []string{purpose}
	token := func(scope string) string {
		if cookie, err := request.Cookie(siteCookie(scope)); err == nil {
			return cookie.Value
		}
		return ""
	}
	var err error
	if purpose == "access" {
		purposes = []string{"admin", "access"}
		err = api.database.RevokeBrowserSiteSessions(request.Context(), token("access"), token("admin"))
	} else {
		err = api.database.RevokeSiteSession(request.Context(), purpose, token(purpose))
	}
	if err != nil {
		writeJSON(response, 503, map[string]string{"detail": "无法撤销访问会话，请重试"})
		return
	}
	for _, scope := range purposes {
		http.SetCookie(response, &http.Cookie{Name: siteCookie(scope), Value: "", Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: -1})
	}
	writeJSON(response, 200, map[string]bool{"success": true})
}
