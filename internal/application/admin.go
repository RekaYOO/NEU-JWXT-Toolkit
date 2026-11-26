package application

import (
	"context"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func (api *API) requireAdmin(response http.ResponseWriter, request *http.Request) bool {
	if !api.siteAuthenticated(request, "admin") {
		writeJSON(response, 401, map[string]string{"detail": "请先验证管理员身份", "error_code": "ADMIN_REQUIRED"})
		return false
	}
	return true
}

func (api *API) adminAccounts(response http.ResponseWriter, request *http.Request) {
	if !api.requireAdmin(response, request) {
		return
	}
	if request.Method == "POST" {
		var input struct {
			Account string `json:"account"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		account, err := campus.NormalizeAccount(input.Account)
		if err != nil {
			writeJSON(response, 422, map[string]string{"detail": "学号无效"})
			return
		}
		if err := api.database.AdmitAccount(request.Context(), account, api.accounts.mode == "remote-single"); err != nil {
			api.documentError(response, err)
			return
		}
		writeJSON(response, 200, map[string]any{"success": true, "account": account})
		return
	}
	accounts, err := api.database.Accounts(request.Context())
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"accounts": accounts, "deployment_mode": api.accounts.mode, "default_administrator": api.adminAccount})
}

func (api *API) adminAccountAction(response http.ResponseWriter, request *http.Request) {
	if !api.requireAdmin(response, request) {
		return
	}
	account, err := campus.NormalizeAccount(request.PathValue("account"))
	if err != nil {
		writeJSON(response, 422, map[string]string{"detail": "学号无效"})
		return
	}
	action := request.PathValue("action")
	if account == api.adminAccount && (action == "disable" || action == "remove") {
		writeJSON(response, 409, map[string]string{"detail": "默认管理员的准入由服务器配置管理"})
		return
	}
	switch action {
	case "stop":
		err = api.accounts.Stop(request.Context(), account, false)
	case "disable", "remove":
		err = api.accounts.ChangeAccess(request.Context(), account, false)
	case "enable":
		err = api.accounts.ChangeAccess(request.Context(), account, true)
	default:
		writeJSON(response, 404, map[string]string{"detail": "管理员操作不存在"})
		return
	}
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "account": account})
}

func (accounts *Accounts) ChangeAccess(ctx context.Context, account string, enabled bool) error {
	accounts.mutex.Lock()
	defer accounts.mutex.Unlock()
	if err := accounts.database.SetAdmission(ctx, account, enabled, accounts.mode == "remote-single"); err != nil {
		return err
	}
	if runtime := accounts.runtimes[account]; runtime != nil {
		runtime.Cancel()
		delete(accounts.runtimes, account)
		go runtime.Client.Close()
	}
	return nil
}
