package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const outlineTTL = 30 * 24 * time.Hour

type outlineSync struct {
	mutex     sync.Mutex
	running   bool
	cancelled bool
	total     int
	completed int
	failed    int
	current   string
	errors    []string
	stop      chan struct{}
}

func (state *outlineSync) snapshot(account string) map[string]any {
	state.mutex.Lock()
	defer state.mutex.Unlock()
	return map[string]any{"account": account, "running": state.running, "cancelled": state.cancelled, "total": state.total, "completed": state.completed, "failed": state.failed, "current_course": state.current, "errors": append([]string{}, state.errors...)}
}

func (api *API) outlineState(session store.Session) *outlineSync {
	value, _ := api.outlineStates.LoadOrStore(session.Account+":"+strconv.FormatInt(session.Generation, 10), &outlineSync{})
	return value.(*outlineSync)
}

func (api *API) registerOutlineResource() {
	api.resources.Register("course-outline-metadata", ResourceSpec{SchemaVersion: 2, TTL: outlineTTL, Offline: true, FetchReason: func(ctx context.Context, runtime *Runtime, variant, reason string) (json.RawMessage, json.RawMessage, error) {
		code := strings.TrimPrefix(variant, "course:")
		payload, err := academic.FetchOutlineMetadata(ctx, runtime, code)
		if err != nil {
			return nil, nil, err
		}
		fingerprint := strings.TrimPrefix(reason, "metadata_sync:")
		if len(fingerprint) != 64 {
			fingerprint = ""
		}
		payload["plan_fingerprint"] = fingerprint
		encoded, err := json.Marshal(payload)
		return encoded, nil, err
	}})
}

func metadataStale(entry store.CacheEntry, payload map[string]any) bool {
	ttl := outlineTTL
	if payload["status"] == "not_found" {
		ttl = 7 * 24 * time.Hour
		if entry.Policy != nil && time.Duration(entry.Policy.IntervalMinutes)*time.Minute > ttl {
			policy := *entry.Policy
			policy.IntervalMinutes = int64(ttl / time.Minute)
			entry.Policy = &policy
		}
	}
	return entry.SchemaVersion != 2 || entry.Stale(ttl) || (payload["status"] != "success" && payload["status"] != "not_found")
}

func (api *API) metadataItems(ctx context.Context, session store.Session, codes []string) []map[string]any {
	items, seen := []map[string]any{}, map[string]bool{}
	for _, code := range codes {
		if seen[code] {
			continue
		}
		seen[code] = true
		entry, err := api.database.ReadCache(ctx, session, "course-outline-metadata", "course:"+code)
		if err != nil {
			continue
		}
		var payload map[string]any
		if json.Unmarshal(entry.Payload, &payload) != nil {
			continue
		}
		payload["needs_sync"] = metadataStale(entry, payload)
		items = append(items, payload)
	}
	return items
}

func (api *API) registerOutlineMetadata(router *http.ServeMux) {
	router.HandleFunc("POST /api/course-outlines/metadata/read", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		var input struct {
			Codes []string `json:"course_codes"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		if len(input.Codes) > 800 {
			writeJSON(response, 422, map[string]string{"detail": "课程数量超过限制"})
			return
		}
		for index, code := range input.Codes {
			input.Codes[index] = strings.TrimSpace(code)
			if !academic.ValidCourseCode(input.Codes[index]) {
				writeJSON(response, 422, map[string]string{"detail": "课程代码无效"})
				return
			}
		}
		writeJSON(response, 200, map[string]any{"items": api.metadataItems(request.Context(), session, input.Codes)})
	})
	router.HandleFunc("GET /api/course-outlines/metadata/plan", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		codes := []string{}
		if entry, err := api.database.ReadCache(request.Context(), session, "academic-report", "default"); err == nil {
			var payload any
			if json.Unmarshal(entry.Payload, &payload) == nil {
				var collect func(any)
				collect = func(value any) {
					switch typed := value.(type) {
					case map[string]any:
						if code, ok := typed["course_code"].(string); ok && academic.ValidCourseCode(code) {
							codes = append(codes, code)
						}
						for _, nested := range typed {
							collect(nested)
						}
					case []any:
						for _, nested := range typed {
							collect(nested)
						}
					}
				}
				collect(payload)
			}
		}
		sort.Strings(codes)
		writeJSON(response, 200, map[string]any{"items": api.metadataItems(request.Context(), session, codes)})
	})
	router.HandleFunc("POST /api/course-outlines/metadata/sync", api.startOutlineSync)
	router.HandleFunc("GET /api/course-outlines/metadata/sync/status", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		writeJSON(response, 200, api.outlineState(session).snapshot(session.Account))
	})
	router.HandleFunc("POST /api/course-outlines/metadata/sync/cancel", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		state := api.outlineState(session)
		state.mutex.Lock()
		if state.running && !state.cancelled {
			close(state.stop)
		}
		state.cancelled = true
		state.mutex.Unlock()
		writeJSON(response, 200, state.snapshot(session.Account))
	})
}

func outlineFingerprint(course map[string]any) string {
	first := func(keys ...string) string {
		for _, key := range keys {
			if value := academic.Text(course[key]); value != "" {
				return value
			}
		}
		return ""
	}
	payload, _ := json.Marshal(map[string]any{"course_code": first("course_code", "code"), "course_name": first("course_name", "name"), "credit": course["credit"], "course_nature": first("course_nature", "course_type"), "plan_term": first("plan_term", "suggest_term", "term_code")})
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:])
}

func (api *API) startOutlineSync(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Courses []map[string]any `json:"courses"`
		Force   bool             `json:"force"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if len(input.Courses) > 800 {
		writeJSON(response, 422, map[string]string{"detail": "课程数量超过限制"})
		return
	}
	courses, seen := []map[string]any{}, map[string]bool{}
	for _, course := range input.Courses {
		code := strings.TrimSpace(academic.Text(course["course_code"]))
		if code == "" {
			code = strings.TrimSpace(academic.Text(course["code"]))
		}
		if !academic.ValidCourseCode(code) {
			writeJSON(response, 422, map[string]string{"detail": "课程代码无效"})
			return
		}
		if !seen[code] {
			course["course_code"] = code
			seen[code] = true
			courses = append(courses, course)
		}
	}
	runtime := api.accounts.Runtime(session)
	if runtime == nil {
		writeJSON(response, 401, map[string]string{"detail": "请先恢复学校登录"})
		return
	}
	state := api.outlineState(session)
	state.mutex.Lock()
	if state.running {
		state.mutex.Unlock()
		result := state.snapshot(session.Account)
		result["accepted"] = false
		writeJSON(response, 200, result)
		return
	}
	state.running, state.cancelled, state.total, state.completed, state.failed, state.current, state.errors = true, false, len(courses), 0, 0, "", nil
	state.stop = make(chan struct{})
	state.mutex.Unlock()
	api.accounts.workers.Add(1)
	go api.runOutlineSync(session, runtime, state, courses, input.Force)
	result := state.snapshot(session.Account)
	result["accepted"] = true
	writeJSON(response, 200, result)
}

func (api *API) runOutlineSync(session store.Session, runtime *Runtime, state *outlineSync, courses []map[string]any, force bool) {
	defer api.accounts.workers.Done()
	defer func() { state.mutex.Lock(); state.running = false; state.current = ""; state.mutex.Unlock() }()
	for _, course := range courses {
		state.mutex.Lock()
		cancelled := state.cancelled
		state.mutex.Unlock()
		if cancelled || runtime.Context.Err() != nil {
			return
		}
		code := academic.Text(course["course_code"])
		fingerprint := outlineFingerprint(course)
		state.mutex.Lock()
		state.current = code
		state.mutex.Unlock()
		entry, err := api.database.ReadCache(runtime.Context, session, "course-outline-metadata", "course:"+code)
		var payload map[string]any
		if err == nil && json.Unmarshal(entry.Payload, &payload) == nil && !force && !metadataStale(entry, payload) && payload["plan_fingerprint"] == fingerprint {
			state.mutex.Lock()
			state.completed++
			state.mutex.Unlock()
			continue
		}
		result, err := api.resources.Submit(runtime.Context, session, "course-outline-metadata", "course:"+code, "metadata_sync:"+fingerprint, true)
		success := false
		if err == nil {
			identifier, _ := result["job_id"].(string)
			if job, found := api.resources.Job(session, identifier); found {
				select {
				case <-state.stop:
					return
				case <-job.done:
					job, _ = api.resources.Job(session, identifier)
					success = job.Status == "completed"
				case <-runtime.Context.Done():
					return
				}
			}
		}
		state.mutex.Lock()
		if success {
			state.completed++
		} else {
			state.failed++
			state.errors = append(state.errors, code)
		}
		state.mutex.Unlock()
	}
}
