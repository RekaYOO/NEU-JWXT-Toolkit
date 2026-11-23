package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const experimentTTL = 5 * time.Minute

func (api *API) registerExperimentResource() {
	api.resources.Register("experiment-courses", ResourceSpec{TTL: experimentTTL, Offline: true, PublicRefresh: true, Fetch: func(ctx context.Context, runtime *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		payload, err := academic.FetchExperimentCourses(ctx, runtime, "")
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(payload)
		return encoded, academic.ExperimentSemantic(payload), err
	}, Diff: academic.ExperimentDiff})
}

func (api *API) registerExperiment(router *http.ServeMux) {
	router.HandleFunc("POST /api/experiment-courses/select", api.experimentMutation)
	router.HandleFunc("POST /api/experiment-courses/deselect", api.experimentMutation)
	router.HandleFunc("POST /api/experiment-courses/verify", api.experimentMutation)
	router.HandleFunc("GET /api/experiment-courses", api.experimentCourses)
	router.HandleFunc("GET /api/experiment-courses/cache", api.cachedExperiments)
	router.HandleFunc("GET /api/offline/experiment-courses", api.offlineHandler(api.cachedExperiments))
	router.HandleFunc("GET /api/experiment-courses/{task_id}/rounds", api.experimentRounds)
}

func (api *API) experimentCourses(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	term := request.URL.Query().Get("term")
	if term != "" && !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "学期代码无效"})
		return
	}
	var payload map[string]any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		payload, err = academic.FetchExperimentCourses(request.Context(), runtime, term)
		return err
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}

func (api *API) cachedExperiments(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "experiment-courses", "default")
	if errors.Is(err, store.ErrMissing) {
		writeJSON(response, 200, map[string]any{"available": false, "term": "", "courses": []any{}, "selected_results": []any{}, "total": 0})
		return
	}
	if err != nil {
		api.resourceError(response, err)
		return
	}
	var payload map[string]any
	if err := json.Unmarshal(entry.Payload, &payload); err != nil {
		api.resourceError(response, err)
		return
	}
	payload["available"], payload["username"], payload["cache"] = true, session.Account, entry.Metadata(experimentTTL)
	writeJSON(response, 200, payload)
}

func (api *API) experimentRounds(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	query := academic.ExperimentQuery{Term: request.URL.Query().Get("term"), Task: request.PathValue("task_id"), Course: request.URL.Query().Get("course_no"), Project: request.URL.Query().Get("project_code")}
	if !query.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "实验班查询参数无效"})
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var rounds []map[string]any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		rounds, err = academic.FetchExperimentRounds(request.Context(), runtime, query)
		return err
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	baseline, available, stale := api.experimentBaseline(request.Context(), session, query.Term)
	for _, round := range rounds {
		academic.ExperimentRoundAvailability(round, query, baseline, available, stale, time.Now())
	}
	writeJSON(response, 200, map[string]any{"rounds": rounds, "total": len(rounds)})
}

func (api *API) experimentBaseline(ctx context.Context, session store.Session, term string) ([]academic.SelectionMeeting, bool, bool) {
	entry, err := api.database.ReadCache(ctx, session, "personal-timetable", "term:"+term)
	if err != nil || entry.SchemaVersion != 1 {
		return nil, false, true
	}
	var payload struct {
		Term    string           `json:"term_code"`
		Courses []map[string]any `json:"courses"`
	}
	if json.Unmarshal(entry.Payload, &payload) != nil || payload.Term != term || payload.Courses == nil {
		return nil, false, true
	}
	meetings := []academic.SelectionMeeting{}
	for _, row := range payload.Courses {
		meetings = append(meetings, academic.NormalizeSelectionMeeting(row, term, "personal_timetable"))
	}
	return meetings, true, entry.Stale(timetableTTL)
}
