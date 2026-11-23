package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (runtime *Runtime) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	if runtime.Context.Err() != nil {
		return nil, store.ErrAccess
	}
	client, ok := runtime.Client.(academic.ServiceRequester)
	if !ok {
		return nil, &campus.Error{Code: "SERVICE_UNAVAILABLE", Message: "子服务不可用"}
	}
	payload, err := client.RequestService(ctx, input)
	var failure *campus.Error
	if !input.ReadOnly || runtime.accounts == nil || !errors.As(err, &failure) || !failure.ConfirmedServiceExpiry {
		return payload, err
	}
	active, primary := runtime.Client.Status()
	mode := input.Mode
	if mode == "" || mode == "inherit" {
		mode = primary
	}
	recoverer, supported := client.(interface {
		RecoverDirectService(context.Context, string, string) error
	})
	if !active || !(mode == "direct" && supported || mode == "webvpn" && runtime.accounts.recoverVPNService != nil) || !runtime.recoveryMutex.TryLock() {
		return nil, err
	}
	defer runtime.recoveryMutex.Unlock()
	session := store.Session{Account: runtime.Client.Account(), Generation: runtime.Generation}
	if runtime.accounts.Runtime(session) != runtime || time.Now().Before(runtime.serviceRetryAfter[input.Service]) {
		return nil, err
	}
	secret, readErr := runtime.accounts.database.ReadSecret(ctx, session.Account, "campus-session")
	var saved savedCampusSession
	if readErr != nil || json.Unmarshal(secret, &saved) != nil || saved.Account != session.Account || saved.Password == "" || ctx.Err() != nil || runtime.accounts.Runtime(session) != runtime {
		return nil, err
	}
	if runtime.serviceRetryAfter == nil {
		runtime.serviceRetryAfter = map[string]time.Time{}
	}
	runtime.serviceRetryAfter[input.Service] = time.Now().Add(time.Minute)
	var recoveryErr error
	if mode == "webvpn" {
		recoveryErr = runtime.accounts.recoverVPNService(ctx, session, runtime, input.Service, saved.Password)
	} else {
		recoveryErr = recoverer.RecoverDirectService(ctx, input.Service, saved.Password)
	}
	if recoveryErr != nil {
		return nil, recoveryErr
	}
	if runtime.accounts.Runtime(session) != runtime || ctx.Err() != nil {
		return nil, store.ErrAccess
	}
	input.RefreshSession = false
	return client.RequestService(ctx, input)
}

func (api *API) registerJWXK(router *http.ServeMux) {
	router.HandleFunc("GET /api/course-selection/jwxk/automation/tasks", api.jwxkAutomationTasks)
	router.HandleFunc("POST /api/course-selection/jwxk/automation/tasks", api.jwxkAutomationTasks)
	router.HandleFunc("POST /api/course-selection/jwxk/automation/tasks/sync-batch-times", api.jwxkSyncTaskTimes)
	router.HandleFunc("POST /api/course-selection/jwxk/automation/tasks/{action}", api.jwxkTaskAction)
	router.HandleFunc("GET /api/course-selection/jwxk/batches/{batch_code}/automation-settings", api.jwxkAutomationSettings)
	router.HandleFunc("PUT /api/course-selection/jwxk/batches/{batch_code}/automation-settings", api.jwxkAutomationSettings)
	router.HandleFunc("GET /api/course-selection/jwxk/status", api.jwxkStatus)
	router.HandleFunc("PUT /api/course-selection/jwxk/settings", api.jwxkSettings)
	router.HandleFunc("POST /api/course-selection/jwxk/courses/search", api.jwxkSearch)
	router.HandleFunc("POST /api/course-selection/jwxk/selected", api.jwxkSelected)
	router.HandleFunc("POST /api/course-selection/jwxk/catalog/search", api.jwxkCatalogSearch)
	router.HandleFunc("GET /api/course-selection/jwxk/catalog/archives", api.jwxkArchives)
	router.HandleFunc("GET /api/course-selection/jwxk/catalog/archives/{archive_id}", api.jwxkArchives)
	router.HandleFunc("DELETE /api/course-selection/jwxk/catalog/archives/{archive_id}", api.jwxkArchives)
	router.HandleFunc("POST /api/course-selection/jwxk/catalog/eligibility", api.jwxkEligibility)
	router.HandleFunc("POST /api/course-selection/jwxk/catalog/detail", api.jwxkDetail)
	router.HandleFunc("POST /api/course-selection/jwxk/catalog/filter-options", api.jwxkFilters)
	router.HandleFunc("POST /api/course-selection/jwxk/batches/confirm", api.jwxkConfirm)
	router.HandleFunc("POST /api/course-selection/jwxk/weights/budget", api.jwxkWeightBudget)
	router.HandleFunc("GET /api/course-selection/jwxk/weights/config", api.jwxkWeightConfig)
	router.HandleFunc("POST /api/course-selection/jwxk/weights/plan", api.jwxkWeightPlan)
	router.HandleFunc("POST /api/course-selection/jwxk/weights/apply", api.jwxkWeightApply)
	router.HandleFunc("POST /api/course-selection/jwxk/weights/application/cancel", api.jwxkCancelWeightApplication)
	router.HandleFunc("POST /api/course-selection/jwxk/schedule", api.jwxkSelectionSchedule)
	router.HandleFunc("POST /api/course-selection/jwxk/plan/preview", api.jwxkPlanPreview)
	router.HandleFunc("POST /api/course-selection/jwxk/plan/read", api.jwxkPlanRead)
	router.HandleFunc("POST /api/course-selection/jwxk/plan/save", api.jwxkPlanSave)
	router.HandleFunc("POST /api/course-selection/jwxk/courses/select", api.jwxkSelect)
	router.HandleFunc("POST /api/course-selection/jwxk/courses/deselect", api.jwxkDeselect)
}

func jwxkProbe(response http.ResponseWriter, request *http.Request) (bool, bool) {
	value, exists := request.URL.Query()["probe"]
	if !exists {
		return true, true
	}
	if len(value) != 1 {
		writeJSON(response, 422, map[string]string{"detail": "探测参数无效"})
		return false, false
	}
	probe, err := strconv.ParseBool(value[0])
	if err != nil {
		writeJSON(response, 422, map[string]string{"detail": "探测参数无效"})
		return false, false
	}
	return probe, true
}

func (api *API) jwxkPreference(ctx context.Context, session store.Session) (string, int64, error) {
	document, err := api.database.Read(ctx, session.Account, "settings", "jwxk")
	if errors.Is(err, store.ErrMissing) {
		return "follow", 0, nil
	}
	if err != nil {
		return "", 0, err
	}
	if document.Deleted {
		return "follow", document.Revision, nil
	}
	var preference struct {
		Mode string `json:"network_mode"`
	}
	if json.Unmarshal(document.Payload, &preference) != nil || (preference.Mode != "follow" && preference.Mode != "direct" && preference.Mode != "webvpn") {
		return "", 0, &campus.Error{Code: "INVALID_SETTINGS", Message: "选课线路配置无效，请重新保存"}
	}
	return preference.Mode, document.Revision, nil
}

func (api *API) jwxkStatus(response http.ResponseWriter, request *http.Request) {
	probe, valid := jwxkProbe(response, request)
	if !valid {
		return
	}
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	preference, _, err := api.jwxkPreference(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	runtime := api.accounts.Runtime(session)
	active, primary := false, "direct"
	if runtime != nil {
		active, primary = runtime.Client.Status()
	}
	effective := preference
	if effective == "follow" {
		effective = primary
	}
	result := map[string]any{"available": false, "network_mode": preference, "effective_network_mode": effective, "cas_service": "https://jwxk.neu.edu.cn/xsxk/auth/cas", "primary_authenticated": active, "current_user": session.Account, "service_authenticated": false, "authenticated": false, "service_auth_state": "checking", "official_time": "", "online_count": nil, "current_campus": "", "current_campus_name": "", "batches": []any{}, "message": "正在核验选课服务", "error_code": nil}
	if !probe {
		writeJSON(response, 200, result)
		return
	}
	if !active {
		result["service_auth_state"], result["message"] = "login_required", "请先恢复学校登录"
		writeJSON(response, 200, result)
		return
	}
	if effective == "webvpn" && api.scopedVPNPendingSnapshot(request, session, "jwxk")["required"] == true {
		result["service_auth_state"], result["message"] = "interaction_required", "请继续当前选课系统验证"
		writeJSON(response, 200, result)
		return
	}
	release, err := api.resources.acquireAccount(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	payload, err := academic.FetchJWXKContext(request.Context(), runtime, effective)
	if err == nil {
		if err := api.saveJWXKRounds(request.Context(), session, payload); err != nil {
			api.documentError(response, err)
			return
		}
		for key, value := range payload {
			result[key] = value
		}
		result["available"], result["authenticated"], result["service_authenticated"], result["service_auth_state"], result["message"] = true, true, true, "authenticated", "选课服务已连接"
	} else {
		result["service_auth_state"], result["message"] = "service_unavailable", "选课服务暂时不可用"
		var failure *campus.Error
		if errors.As(err, &failure) {
			result["error_code"], result["message"] = failure.Code, failure.Message
			switch failure.Code {
			case "SERVICE_INTERACTION_REQUIRED":
				result["service_auth_state"] = "interaction_required"
			case "JWXK_NOT_IN_SELECTION_ROUND":
				result["service_auth_state"] = "not_in_selection_round"
			case "WEBVPN_CAMPUS_NETWORK":
				result["service_auth_state"] = "campus_network_blocked"
			case "SERVICE_AUTH_REQUIRED", "SERVICE_NOT_SENT":
				result["service_auth_state"] = "login_required"
			case "UPSTREAM_UNAVAILABLE":
				result["service_auth_state"] = "network_unreachable"
			}
		}
	}
	writeJSON(response, 200, result)
}

func (api *API) jwxkSettings(response http.ResponseWriter, request *http.Request) {
	if _, valid := jwxkProbe(response, request); !valid {
		return
	}
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Mode string `json:"network_mode"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Mode != "follow" && input.Mode != "direct" && input.Mode != "webvpn" {
		writeJSON(response, 422, map[string]string{"detail": "选课线路无效"})
		return
	}
	document, err := api.database.Read(request.Context(), session.Account, "settings", "jwxk")
	revision := int64(0)
	if err == nil {
		revision = document.Revision
	} else if !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	encoded, _ := json.Marshal(map[string]string{"network_mode": input.Mode})
	if _, err := api.database.SaveForSession(request.Context(), session, "settings", "jwxk", encoded, revision, false); err != nil {
		api.documentError(response, err)
		return
	}
	api.jwxkStatus(response, request)
}
