package application

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (runtime *Runtime) EvaluationRequest(ctx context.Context, path string, body json.RawMessage) ([]byte, error) {
	requestContext, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	if runtime.Context.Err() != nil {
		return nil, store.ErrAccess
	}
	client, ok := runtime.Client.(academic.EvaluationRequester)
	if !ok {
		return nil, &campus.Error{Code: "EVALUATION_UNAVAILABLE", Message: "评教服务不可用"}
	}
	return client.EvaluationRequest(requestContext, path, body)
}

func (api *API) registerEvaluation(router *http.ServeMux) {
	router.HandleFunc("POST /api/evaluation/submit", api.evaluationSubmit)
	router.HandleFunc("POST /api/evaluation/batch", api.evaluationBatch)
	for _, path := range []string{"/api/evaluation/cycles", "/api/evaluation/tasks", "/api/evaluation/tasks/{task}/courses", "/api/evaluation/courses/{record}/indicators"} {
		router.HandleFunc("GET "+path, api.evaluationRead)
	}
}

func (api *API) evaluationRead(response http.ResponseWriter, request *http.Request) {
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	cycle := request.URL.Query().Get("xnxq")
	task, record := request.PathValue("task"), request.PathValue("record")
	if record != "" {
		task = request.URL.Query().Get("task_id")
	}
	if (cycle != "" && (!academic.ValidEvaluationID(cycle) || len(cycle) > 32)) || (task != "" && !academic.ValidEvaluationID(task)) || (record != "" && (!academic.ValidEvaluationID(record) || task == "")) {
		writeJSON(response, 422, map[string]string{"detail": "评教查询参数无效"})
		return
	}
	var payload map[string]any
	var err error
	switch {
	case request.URL.Path == "/api/evaluation/cycles":
		payload, err = academic.FetchEvaluationCycles(request.Context(), runtime)
	case record != "":
		payload, err = academic.FetchEvaluationIndicators(request.Context(), runtime, task, record, cycle)
	case task != "":
		payload, err = academic.FetchEvaluationCourses(request.Context(), runtime, task, cycle)
	default:
		payload, err = academic.FetchEvaluationTasks(request.Context(), runtime, cycle)
	}
	if err != nil {
		api.evaluationError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}
