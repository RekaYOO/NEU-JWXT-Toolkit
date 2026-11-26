package application

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const accessCookie = "neu_jwxt_access"

func publicSitePath(path string) bool {
	return path == "/api/health" || path == "/api/access/status" || path == "/api/access/login" || path == "/api/access/logout" || remoteRecoveryPath(path)
}

func (api *API) ConfigureRemoteAccess(ctx context.Context, accessPassword string) error {
	if api.accounts.mode == "local-single" {
		return nil
	}
	configured, err := api.database.SitePasswordConfigured(ctx, "access")
	if err != nil || configured {
		return err
	}
	if accessPassword == "" {
		return errors.New("initial access password must be supplied through its environment variable")
	}
	return api.database.SetSitePassword(ctx, "access", accessPassword, false)
}

func (api *API) siteAuthenticated(request *http.Request) bool {
	cookie, err := request.Cookie(accessCookie)
	return err == nil && api.database.SiteSession(request.Context(), "access", cookie.Value)
}

func (api *API) ConfigureDefaultAdministrator(ctx context.Context, account string) error {
	if api.accounts.mode == "local-single" {
		return nil
	}
	if account == "" {
		selected, err := api.database.DefaultAdministrator(ctx)
		if err != nil {
			return err
		}
		api.adminAccount = selected
		return nil
	}
	account, err := campus.NormalizeAccount(account)
	if err != nil {
		return errors.New("NEU_JWXT_ADMIN_ACCOUNT must be a valid school account")
	}
	if err := api.database.AdmitAccount(ctx, account, api.accounts.mode == "remote-single"); err != nil {
		return fmt.Errorf("cannot grant admission to the configured administrator: %w", err)
	}
	if _, err := api.database.Generation(ctx, account); err != nil {
		return fmt.Errorf("the configured administrator must have enabled admission: %w", err)
	}
	api.adminAccount = account
	return nil
}

func (api *API) isDefaultAdministrator(request *http.Request) bool {
	if api.adminAccount == "" {
		return false
	}
	session, err := api.session(request)
	return err == nil && session.Account == api.adminAccount
}

func (api *API) registerSiteAccess(router *http.ServeMux) {
	router.HandleFunc("GET /api/access/status", api.accessStatus)
	router.HandleFunc("POST /api/access/login", api.siteLogin)
	router.HandleFunc("POST /api/access/logout", api.siteLogout)
	router.HandleFunc("GET /api/admin/status", api.adminStatus)
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
	writeJSON(response, 200, map[string]any{"required": api.gatewayRequired, "configured": configured, "authenticated": !api.gatewayRequired || api.siteAuthenticated(request), "profile": api.runtimeInfo()["profile"], "site_default": theme})
}

func (api *API) adminStatus(response http.ResponseWriter, request *http.Request) {
	writeJSON(response, 200, map[string]bool{"eligible": api.isDefaultAdministrator(request)})
}

func (api *API) siteLogin(response http.ResponseWriter, request *http.Request) {
	if !api.gatewayRequired {
		writeJSON(response, 200, map[string]bool{"success": true})
		return
	}
	var input struct {
		Password string `json:"password"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	configured, err := api.database.SitePasswordConfigured(request.Context(), "access")
	if err != nil || !configured {
		writeJSON(response, 503, map[string]string{"detail": "服务器尚未完成密码初始化"})
		return
	}
	address := api.clientSource(request)
	key := address
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
	token, err := api.database.CreateSiteSession(request.Context(), "access", input.Password, lifetime)
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
	http.SetCookie(response, &http.Cookie{Name: accessCookie, Value: token, Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: int(lifetime.Seconds())})
	writeJSON(response, 200, map[string]bool{"success": true})
}

func (api *API) siteLogout(response http.ResponseWriter, request *http.Request) {
	if cookie, err := request.Cookie(accessCookie); err == nil {
		if err := api.database.RevokeSiteSession(request.Context(), "access", cookie.Value); err != nil {
			writeJSON(response, 503, map[string]string{"detail": "无法撤销访问会话，请重试"})
			return
		}
	}
	http.SetCookie(response, &http.Cookie{Name: accessCookie, Value: "", Path: "/", HttpOnly: true, Secure: api.SecureCookies || request.TLS != nil, SameSite: http.SameSiteLaxMode, MaxAge: -1})
	writeJSON(response, 200, map[string]bool{"success": true})
}
