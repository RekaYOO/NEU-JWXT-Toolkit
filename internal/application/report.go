package application

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) waitResource(request *http.Request, session store.Session, result map[string]any) (CacheJob, bool) {
	identifier, _ := result["job_id"].(string)
	job, found := api.resources.Job(session, identifier)
	if !found {
		return CacheJob{}, false
	}
	timer := time.NewTimer(25 * time.Second)
	defer timer.Stop()
	select {
	case <-job.done:
	case <-timer.C:
	case <-request.Context().Done():
		return CacheJob{}, false
	}
	return api.resources.Job(session, identifier)
}

func (api *API) refreshAndWait(response http.ResponseWriter, request *http.Request, resource string) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	result, err := api.resources.Submit(request.Context(), session, resource, "default", "manual", true)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	job, found := api.waitResource(request, session, result)
	if found && job.Status == "completed" {
		writeJSON(response, 200, map[string]any{"success": true, "job_id": job.ID, "revision": job.Revision, "changed": job.Changed, "diff": job.Diff})
		return
	}
	if found && (job.Status == "running" || job.Status == "queued") {
		writeJSON(response, 202, map[string]any{"success": false, "status": job.Status, "job_id": job.ID, "message": "后台刷新仍在进行"})
		return
	}
	writeJSON(response, 503, map[string]any{"detail": "刷新未完成，已保留旧缓存", "job_id": job.ID, "error_kind": job.ErrorKind})
}

func (api *API) registerReport(router *http.ServeMux) {
	router.HandleFunc("GET /api/academic-report/export", api.exportReport)
	router.HandleFunc("GET /api/academic-report/cache", func(response http.ResponseWriter, request *http.Request) {
		api.readReport(response, request, false, false)
	})
	router.HandleFunc("GET /api/academic-report", func(response http.ResponseWriter, request *http.Request) {
		api.readReport(response, request, true, false)
	})
	router.HandleFunc("GET /api/academic-report/summary", func(response http.ResponseWriter, request *http.Request) {
		api.readReport(response, request, true, true)
	})
	router.HandleFunc("POST /api/academic-report/refresh", func(response http.ResponseWriter, request *http.Request) {
		api.refreshAndWait(response, request, "academic-report")
	})
}

func (api *API) readReport(response http.ResponseWriter, request *http.Request, refresh, summary bool) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "academic-report", "default")
	force := request.URL.Query().Get("refresh") == "true"
	if refresh && (err != nil || force || entry.Stale(scoresTTL)) {
		reason := "page_swr"
		if force {
			reason = "manual"
		}
		result, submitErr := api.resources.Submit(request.Context(), session, "academic-report", "default", reason, force)
		if submitErr != nil && err != nil {
			api.resourceError(response, submitErr)
			return
		}
		if err != nil || force {
			api.waitResource(request, session, result)
			entry, err = api.database.ReadCache(request.Context(), session, "academic-report", "default")
		}
	}
	if err != nil {
		api.documentError(response, err)
		return
	}
	var payload map[string]any
	if err := json.Unmarshal(entry.Payload, &payload); err != nil {
		api.resourceError(response, err)
		return
	}
	if summary {
		categories := []map[string]any{}
		var collect func([]any)
		collect = func(nodes []any) {
			for _, raw := range nodes {
				node, ok := raw.(map[string]any)
				if !ok {
					continue
				}
				item := map[string]any{}
				for _, field := range []string{"name", "path", "path_array", "is_leaf", "required_credits", "passed_credits", "selected_credits", "earned_credits", "remaining_credits", "completion_rate", "is_completed"} {
					item[field] = node[field]
				}
				courses, _ := node["courses"].([]any)
				item["course_count"] = len(courses)
				categories = append(categories, item)
				children, _ := node["children"].([]any)
				collect(children)
			}
		}
		nodes, _ := payload["categories"].([]any)
		collect(nodes)
		payload = map[string]any{"student_info": map[string]any{"name": payload["student_name"], "student_id": payload["student_id"], "major": payload["major"], "college": payload["college"]},
			"program_info": map[string]any{"name": payload["program_name"], "code": payload["program_code"]}, "credit_summary": payload["credit_summary"], "category_summary": categories, "calculated_time": payload["calculated_time"]}
	}
	payload["source"], payload["is_fresh"], payload["last_update"], payload["cache"] = "local", !entry.Stale(scoresTTL), entry.SavedAt, entry.Metadata(scoresTTL)
	if request.Context().Value(offlineIdentityKey{}) != nil {
		payload["source"], payload["is_fresh"] = "offline", false
	}
	writeJSON(response, 200, payload)
}
