package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

var weightSolverSlots = make(chan struct{}, 2)

func (api *API) jwxkWeightConfig(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	term := request.URL.Query().Get("term_code")
	if !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() || term == "" {
		writeJSON(response, 422, map[string]string{"detail": "学期参数无效"})
		return
	}
	value := 5000
	document, err := api.database.Read(request.Context(), session.Account, "settings", "weight-grade-"+term)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	if err == nil && !document.Deleted {
		var payload struct {
			Grade int `json:"grade_size"`
		}
		if json.Unmarshal(document.Payload, &payload) == nil && payload.Grade >= 1 && payload.Grade <= 100000 && payload.Grade != 360 {
			value = payload.Grade
		}
		if payload.Grade != value {
			encoded, _ := json.Marshal(map[string]int{"grade_size": value})
			if _, err := api.database.SaveForSession(request.Context(), session, "settings", "weight-grade-"+term, encoded, document.Revision, false); err != nil {
				api.documentError(response, err)
				return
			}
		}
	}
	result := map[string]any{"term_code": term, "grade_size": value}
	if batch := request.URL.Query().Get("batch_code"); batch != "" {
		if !(academic.JWXKSearch{Batch: batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
			writeJSON(response, 422, map[string]string{"detail": "轮次参数无效"})
			return
		}
		pending, err := api.pendingWeightApplication(request.Context(), session, batch, term)
		if err != nil {
			api.documentError(response, err)
			return
		}
		result["pending_application"] = pending
	}
	writeJSON(response, 200, result)
}

func (api *API) jwxkWeightPlan(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.JWXKWeightPlan
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "方案或年级人数无效"})
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	ctx, cancel := context.WithCancel(request.Context())
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	mode, _, err := api.jwxkPreference(ctx, session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if mode == "follow" {
		_, mode = runtime.Client.Status()
	}
	_, archive, err := api.readJWXKArchive(ctx, session, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if archive == nil || archive["catalog_complete"] != true || archive["selection_type_code"] != "04" || archive["term_code"] != input.Term {
		writeJSON(response, 409, map[string]string{"detail": "本学期权重轮次完整目录尚未就绪，请稍后再生成策略"})
		return
	}
	id, err := api.resources.startDocumentWork(session, "jwxk-catalog", input.Batch, func(ctx context.Context, runtime *Runtime) error {
		return api.scanJWXKCatalog(ctx, session, runtime, input.Batch, mode)
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	job, ok := api.resources.Job(session, id)
	if !ok {
		api.documentError(response, store.ErrMissing)
		return
	}
	select {
	case <-job.done:
	case <-ctx.Done():
		api.resourceError(response, ctx.Err())
		return
	}
	job, _ = api.resources.Job(session, id)
	if job.Status != "completed" {
		writeJSON(response, 409, map[string]string{"detail": "本次市场刷新未完成，暂不能生成策略"})
		return
	}
	release, err := api.resources.acquireAccount(ctx, session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	_, archive, err = api.readJWXKArchive(ctx, session, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	scanID := academic.Text(archive["completed_scan_id"])
	rows := []map[string]any{}
	for _, row := range jwxkArchiveRows(archive["courses"]) {
		if scanID != "" && row["market_scan_id"] == scanID {
			rows = append(rows, row)
		}
	}
	if archive == nil || archive["sync_status"] != "complete" || len(rows) == 0 || len(rows) != int(academic.Number(archive["completed_course_count"])) {
		writeJSON(response, 409, map[string]string{"detail": "市场快照已变化或已删除，请重新生成策略"})
		return
	}
	official, err := academic.FetchJWXKSelected(ctx, runtime, mode, input.Batch)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	budget, err := academic.FetchJWXKWeightBudget(ctx, runtime, mode, input.Batch)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	select {
	case weightSolverSlots <- struct{}{}:
		defer func() { <-weightSolverSlots }()
	default:
		writeJSON(response, 429, map[string]string{"detail": "模型计算繁忙，请稍后重试"})
		return
	}
	payload, err := academic.BuildJWXKWeightPlan(ctx, input, rows, official, budget)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	key := "weight-grade-" + input.Term
	document, err := api.database.Read(ctx, session.Account, "settings", key)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	encoded, _ := json.Marshal(map[string]int{"grade_size": input.GradeSize})
	if _, err := api.database.SaveForSession(ctx, session, "settings", key, encoded, document.Revision, false); err != nil {
		api.documentError(response, err)
		return
	}
	if err := api.syncJWXKSelectedSnapshot(ctx, session, input.Batch, official); err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}
