package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"sort"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const festivalTTL = 30 * time.Minute

func festivalFailure(err error) *campus.Error {
	code, message := "CXCY_SERVICE_UNAVAILABLE", "创院系统暂时不可用，已保存的活动仍可查看"
	var failure *campus.Error
	if errors.As(err, &failure) {
		switch failure.Code {
		case "SERVICE_INTERACTION_REQUIRED":
			code, message = failure.Code, failure.Message
		case "AUTH_REQUIRED", "AUTH_EXPIRED", "SERVICE_AUTH_REQUIRED", "SERVICE_NOT_SENT", "CXCY_LOGIN_REQUIRED":
			code, message = "CXCY_LOGIN_REQUIRED", "创院系统登录已失效，请恢复创院系统登录"
		case "UPSTREAM_UNAVAILABLE", "CXCY_NETWORK_UNREACHABLE":
			code, message = "CXCY_NETWORK_UNREACHABLE", "当前创院线路不可达，请检查网络或切换线路"
		case "WEBVPN_CAMPUS_NETWORK", "WEBVPN_CAMPUS_NETWORK_BLOCKED":
			code, message = "WEBVPN_CAMPUS_NETWORK_BLOCKED", "校园网环境无法使用WebVPN，请切换创院系统线路"
		}
	}
	return &campus.Error{Code: code, Message: message}
}

func (api *API) festivalPreference(ctx context.Context, session store.Session) (string, int64, error) {
	document, err := api.database.Read(ctx, session.Account, "settings", "cxcy")
	if errors.Is(err, store.ErrMissing) {
		return "follow", 0, nil
	}
	if err != nil {
		return "", 0, err
	}
	if document.Deleted {
		return "follow", document.Revision, nil
	}
	var value struct {
		Mode string `json:"network_mode"`
	}
	if json.Unmarshal(document.Payload, &value) != nil || value.Mode != "follow" && value.Mode != "direct" && value.Mode != "webvpn" {
		return "", 0, errors.New("创院线路配置无效，请重新保存")
	}
	return value.Mode, document.Revision, nil
}

func festivalMode(preference string, runtime *Runtime) string {
	if preference != "follow" {
		return preference
	}
	if runtime != nil {
		_, mode := runtime.Client.Status()
		if mode == "webvpn" {
			return mode
		}
	}
	return "direct"
}

func (api *API) fetchFestival(ctx context.Context, runtime *Runtime) (academic.FestivalSnapshot, error) {
	session := store.Session{Account: runtime.Client.Account(), Generation: runtime.Generation}
	preference, _, err := api.festivalPreference(ctx, session)
	if err != nil {
		return academic.FestivalSnapshot{}, err
	}
	result, err := academic.FetchFestivalActivities(ctx, runtime, festivalMode(preference, runtime))
	if err != nil {
		return academic.FestivalSnapshot{}, festivalFailure(err)
	}
	return result, nil
}

func (api *API) registerFestivalResource() {
	api.resources.Register("festival-activities", ResourceSpec{TTL: festivalTTL, Offline: true, PublicRefresh: true, AuthScope: "cxcy", Fetch: func(ctx context.Context, runtime *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		result, err := api.fetchFestival(ctx, runtime)
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(result)
		sort.Slice(result.Activities, func(left, right int) bool {
			first, second := result.Activities[left], result.Activities[right]
			return first.Section+":"+first.ID < second.Section+":"+second.ID
		})
		sort.Strings(result.Warnings)
		semantic, _ := json.Marshal(result)
		return encoded, semantic, err
	}})
}

func (api *API) registerFestival(router *http.ServeMux) {
	router.HandleFunc("POST /api/export/festival-activities/certificates/archive", api.festivalArchive)
	router.HandleFunc("GET /api/export/festival-activities", api.festivalActivities)
	router.HandleFunc("GET /api/export/festival-activities/cache", api.cachedFestival)
	router.HandleFunc("DELETE /api/export/festival-activities/cache", api.deleteFestivalCache)
	router.HandleFunc("GET /api/offline/festival-activities", api.offlineHandler(api.cachedFestival))
	router.HandleFunc("GET /api/export/festival-activities/status", api.festivalStatus)
	router.HandleFunc("PUT /api/export/festival-activities/settings", api.festivalSettings)
}

func publicFestival(snapshot academic.FestivalSnapshot, session store.Session, source string) map[string]any {
	encoded, _ := json.Marshal(snapshot.Activities)
	rows := []map[string]any{}
	json.Unmarshal(encoded, &rows)
	if rows == nil {
		rows = []map[string]any{}
	}
	for _, row := range rows {
		delete(row, "certificate_url")
		delete(row, "detail_url")
	}
	if snapshot.Warnings == nil {
		snapshot.Warnings = []string{}
	}
	return map[string]any{"available": true, "username": session.Account, "source": source, "activities": rows, "warnings": snapshot.Warnings, "total": len(rows), "cache": nil}
}

func (api *API) festivalError(response http.ResponseWriter, err error) {
	failure := festivalFailure(err)
	status := 503
	if failure.Code == "CXCY_LOGIN_REQUIRED" {
		status = 401
	}
	writeJSON(response, status, map[string]any{"detail": map[string]string{"message": failure.Message, "error_code": failure.Code, "auth_scope": "cxcy"}, "error_code": failure.Code, "auth_scope": "cxcy"})
}

func (api *API) festivalActivities(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var snapshot academic.FestivalSnapshot
	err := api.resources.documentWorkStep(request.Context(), session, func() error { var err error; snapshot, err = api.fetchFestival(request.Context(), runtime); return err })
	if err != nil {
		api.festivalError(response, err)
		return
	}
	writeJSON(response, 200, publicFestival(snapshot, session, "remote"))
}

func (api *API) cachedFestival(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	source := "cache"
	if _, offline := request.Context().Value(offlineIdentityKey{}).(store.Session); offline {
		source = "offline"
	}
	entry, err := api.database.ReadCache(request.Context(), session, "festival-activities", "default")
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.resourceError(response, err)
		return
	}
	var snapshot academic.FestivalSnapshot
	available := err == nil
	if available && json.Unmarshal(entry.Payload, &snapshot) != nil {
		api.resourceError(response, errors.New("活动缓存格式异常"))
		return
	}
	result := publicFestival(snapshot, session, source)
	result["available"] = available
	if available {
		result["cache"] = entry.Metadata(festivalTTL)
	}
	writeJSON(response, 200, result)
}

func (api *API) deleteFestivalCache(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	deleted, cancelled, err := api.resources.Delete(request.Context(), session, "festival-activities", "default")
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "deleted": deleted, "cancelled_jobs": cancelled})
}

func (api *API) festivalStatus(response http.ResponseWriter, request *http.Request) {
	probe, valid := jwxkProbe(response, request)
	if !valid {
		return
	}
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	preference, _, err := api.festivalPreference(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	runtime := api.accounts.Runtime(session)
	active := false
	if runtime != nil {
		active, _ = runtime.Client.Status()
	}
	mode := festivalMode(preference, runtime)
	result := map[string]any{"network_mode": preference, "effective_network_mode": mode, "primary_authenticated": active, "current_user": session.Account, "service_authenticated": false, "service_auth_state": "checking", "message": "创院线路设置已保存，等待核验", "error_code": nil, "auth_scope": "cxcy"}
	if !active {
		result["service_auth_state"], result["message"], result["error_code"] = "login_required", "请先恢复学校登录", "CXCY_LOGIN_REQUIRED"
	} else if probe && mode == "webvpn" && api.scopedVPNPendingSnapshot(request, session, "cxcy")["required"] == true {
		result["service_auth_state"], result["message"] = "interaction_required", "请继续当前创院系统验证"
	} else if probe {
		ctx, cancel := context.WithTimeout(request.Context(), 8*time.Second)
		defer cancel()
		err = api.resources.documentWorkStep(ctx, session, func() error {
			_, err := runtime.RequestService(ctx, campus.ServiceRequest{Service: "cxcy", Mode: mode, Method: "GET", Path: "/popscience/comp/ucenter/main/index", ReadOnly: true})
			return err
		})
		if err == nil {
			result["service_authenticated"], result["service_auth_state"], result["message"] = true, "authenticated", "创院系统会话有效"
		} else {
			failure := festivalFailure(err)
			state := "service_unavailable"
			switch failure.Code {
			case "SERVICE_INTERACTION_REQUIRED":
				state = "interaction_required"
			case "CXCY_LOGIN_REQUIRED":
				state = "login_required"
			case "CXCY_NETWORK_UNREACHABLE":
				state = "network_unreachable"
			case "WEBVPN_CAMPUS_NETWORK_BLOCKED":
				state = "campus_network_blocked"
			}
			result["service_auth_state"], result["message"], result["error_code"] = state, failure.Message, failure.Code
		}
	}
	writeJSON(response, 200, result)
}

func (api *API) festivalSettings(response http.ResponseWriter, request *http.Request) {
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
		writeJSON(response, 422, map[string]string{"detail": "创院线路无效"})
		return
	}
	document, err := api.database.Read(request.Context(), session.Account, "settings", "cxcy")
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.resourceError(response, err)
		return
	}
	encoded, _ := json.Marshal(map[string]string{"network_mode": input.Mode})
	if _, err := api.database.SaveForSession(request.Context(), session, "settings", "cxcy", encoded, document.Revision, false); err != nil {
		api.resourceError(response, err)
		return
	}
	api.festivalStatus(response, request)
}
