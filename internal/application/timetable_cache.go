package application

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const timetableTTL = 5 * time.Minute

func timetableAllowed(index map[string]any) []string {
	current := academic.Text(index["current"])
	if current == "" {
		return []string{}
	}
	encoded, _ := json.Marshal(index["terms"])
	var terms []map[string]any
	json.Unmarshal(encoded, &terms)
	keys := []string{}
	seen := map[string]bool{}
	confirmed := false
	for _, term := range terms {
		code := academic.Text(term["code"])
		if code == current && term["current"] == true {
			confirmed = true
		}
		if code != "" && !seen[code] {
			keys = append(keys, code)
			seen[code] = true
		}
	}
	if !confirmed {
		return []string{}
	}
	sort.Strings(keys)
	result := []string{current}
	for position, code := range keys {
		if code == current && position+1 < len(keys) {
			result = append(result, keys[position+1])
			break
		}
	}
	return result
}

func (api *API) registerTimetableResources() {
	api.resources.Register("timetable-index", ResourceSpec{TTL: 10 * time.Minute, Offline: true, PublicRefresh: true, Fetch: func(ctx context.Context, runtime *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		payload, err := academic.FetchTimetableTerms(ctx, runtime)
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(payload)
		return encoded, nil, err
	}, After: func(session store.Session, encoded json.RawMessage) {
		var index map[string]any
		if json.Unmarshal(encoded, &index) != nil {
			return
		}
		runtime := api.accounts.Runtime(session)
		if runtime == nil {
			return
		}
		for _, term := range timetableAllowed(index) {
			api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+term, "index_ready", false)
		}
	}})
	api.resources.Register("personal-timetable", ResourceSpec{SchemaVersion: 3, TTL: timetableTTL, Offline: true, Dependencies: []string{"timetable-index"}, Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		term := strings.TrimPrefix(variant, "term:")
		if variant == term || !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() {
			return nil, nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "个人课表学期无效"}
		}
		session := store.Session{Account: runtime.Client.Account(), Generation: runtime.Generation}
		var index map[string]any
		entry, err := api.database.ReadCache(ctx, session, "timetable-index", "default")
		if err != nil || entry.Stale(10*time.Minute) || json.Unmarshal(entry.Payload, &index) != nil {
			index, err = academic.FetchTimetableTerms(ctx, runtime)
			if err != nil {
				return nil, nil, err
			}
		}
		allowed := false
		for _, code := range timetableAllowed(index) {
			allowed = allowed || code == term
		}
		if !allowed {
			return nil, nil, &campus.Error{Code: "HISTORICAL_TIMETABLE", Message: "个人缓存仅支持当前及紧邻下一学期，其他学期请使用实时查询"}
		}
		payload, err := academic.FetchPersonalTimetable(ctx, runtime, term)
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(payload)
		if err != nil {
			return nil, nil, err
		}
		semantic, err := academic.TimetableSemantic(payload)
		return encoded, semantic, err
	}})
}

func personalPayload(entry store.CacheEntry) (map[string]any, error) {
	var payload map[string]any
	if err := json.Unmarshal(entry.Payload, &payload); err != nil {
		return nil, err
	}
	payload["source"], payload["is_fresh"], payload["last_update"], payload["cache"] = "local", !entry.Stale(timetableTTL), entry.SavedAt, entry.Metadata(timetableTTL)
	return payload, nil
}

func (api *API) timetableSnapshot(ctx context.Context, session store.Session) map[string]any {
	result := map[string]any{"terms": []any{}, "current": nil, "index_cache": map[string]any{}, "personal": []any{}}
	if entry, err := api.database.ReadCache(ctx, session, "timetable-index", "default"); err == nil {
		var index map[string]any
		if json.Unmarshal(entry.Payload, &index) == nil {
			result["terms"], result["current"], result["index_cache"] = index["terms"], index["current"], entry.Metadata(10*time.Minute)
		}
	}
	personal := []map[string]any{}
	variants, _ := api.database.CacheVariants(ctx, session, "personal-timetable")
	for _, variant := range variants {
		if entry, err := api.database.ReadCache(ctx, session, "personal-timetable", variant); err == nil && entry.SchemaVersion == 3 {
			if payload, err := personalPayload(entry); err == nil {
				personal = append(personal, payload)
			}
		}
	}
	result["personal"] = personal
	return result
}

func (api *API) registerPersonalTimetable(router *http.ServeMux) {
	router.HandleFunc("GET /api/timetable/bootstrap", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		writeJSON(response, 200, api.timetableSnapshot(request.Context(), session))
	})
	router.HandleFunc("GET /api/timetable/personal", api.personalTimetable)
	router.HandleFunc("POST /api/timetable/sync", api.syncTimetable)
}

func (api *API) personalTimetable(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	term := request.URL.Query().Get("term_code")
	if !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "学期代码无效"})
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "personal-timetable", "term:"+term)
	if err == nil && entry.SchemaVersion != 3 {
		err = store.ErrMissing
	}
	force := request.URL.Query().Get("refresh") == "true"
	if err != nil || force || entry.Stale(timetableTTL) {
		result, submitErr := api.resources.Submit(request.Context(), session, "personal-timetable", "term:"+term, "page_swr", force)
		if submitErr != nil && err != nil {
			api.resourceError(response, submitErr)
			return
		}
		if err != nil || force {
			api.waitResource(request, session, result)
			entry, err = api.database.ReadCache(request.Context(), session, "personal-timetable", "term:"+term)
			if err == nil && entry.SchemaVersion != 3 {
				err = store.ErrMissing
			}
		}
	}
	if err != nil {
		writeJSON(response, 503, map[string]string{"detail": "暂时无法获取个人课表且没有本地缓存"})
		return
	}
	payload, err := personalPayload(entry)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}

func (api *API) syncTimetable(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	input := struct {
		Term  *string `json:"term_code"`
		Next  bool    `json:"include_next"`
		Force bool    `json:"force"`
	}{Next: true}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Term != nil && !(academic.TimetableContext{Mode: "personal", Term: *input.Term}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "学期代码无效"})
		return
	}
	jobs := []map[string]any{}
	result, err := api.resources.Submit(request.Context(), session, "timetable-index", "default", "page_swr", input.Force)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	jobs = append(jobs, result)
	var index map[string]any
	if entry, err := api.database.ReadCache(request.Context(), session, "timetable-index", "default"); err == nil {
		json.Unmarshal(entry.Payload, &index)
	}
	candidates := timetableAllowed(index)
	if !input.Next && len(candidates) > 1 {
		candidates = candidates[:1]
	}
	if input.Term != nil {
		selected := []string{*input.Term}
		if input.Next && len(candidates) > 1 && *input.Term == candidates[0] {
			selected = append(selected, candidates[1])
		}
		candidates = selected
	}
	for _, term := range candidates {
		result, err := api.resources.Submit(request.Context(), session, "personal-timetable", "term:"+term, "page_swr", input.Force)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		jobs = append(jobs, result)
	}
	writeJSON(response, 200, map[string]any{"jobs": jobs})
}
