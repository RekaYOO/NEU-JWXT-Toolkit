package application

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

type evaluationBatchInput struct {
	Task     string                     `json:"task_id"`
	Cycle    string                     `json:"xnxq,omitempty"`
	DryRun   *bool                      `json:"dry_run"`
	Strategy string                     `json:"strategy"`
	Scores   map[string]json.RawMessage `json:"custom_scores"`
	Records  []string                   `json:"xspjids"`
	Delay    *float64                   `json:"delay"`
}

func (api *API) evaluationBatch(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var input evaluationBatchInput
	if !decodeJSON(response, request, &input) {
		return
	}
	base := academic.EvaluationInput{Task: input.Task, Record: "validate", Cycle: input.Cycle, DryRun: input.DryRun, Strategy: input.Strategy, Scores: input.Scores}
	if err := base.Validate(); err != nil {
		api.evaluationError(response, err)
		return
	}
	delay := 2.0
	if input.Delay != nil {
		delay = *input.Delay
	}
	if math.IsNaN(delay) || math.IsInf(delay, 0) || delay < 0 || delay > 5 || len(input.Records) > 50 || (input.Records != nil && len(input.Records) == 0) {
		writeJSON(response, 422, map[string]string{"detail": "批量数量或间隔无效"})
		return
	}
	selected := map[string]bool{}
	for _, record := range input.Records {
		if !academic.ValidEvaluationID(record) || selected[record] {
			writeJSON(response, 422, map[string]string{"detail": "批量课程标识无效或重复"})
			return
		}
		selected[record] = true
	}
	ctx, cancel := context.WithCancel(request.Context())
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	release, err := api.resources.acquireAccount(ctx, session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	catalog, err := academic.FetchEvaluationCourses(ctx, runtime, input.Task, input.Cycle)
	if err != nil {
		api.evaluationError(response, err)
		return
	}
	pending := []map[string]any{}
	found := map[string]bool{}
	for _, course := range catalog["courses"].([]map[string]any) {
		record := academic.Text(course["xspjid"])
		found[record] = true
		if course["is_evaluated"] != true && (len(selected) == 0 || selected[record]) {
			pending = append(pending, course)
		}
	}
	for record := range selected {
		if !found[record] {
			writeJSON(response, 422, map[string]string{"detail": "所选课程不属于该评教任务"})
			return
		}
	}
	if len(pending) > 50 {
		writeJSON(response, 422, map[string]string{"detail": "每批最多评价50门课程"})
		return
	}
	results := []map[string]any{}
	success := 0
	stopped := false
	for index, course := range pending {
		if ctx.Err() != nil {
			stopped = true
			break
		}
		base.Record = academic.Text(course["xspjid"])
		result, err := api.evaluateOne(ctx, runtime, session, base)
		if err != nil {
			result = map[string]any{"success": false, "message": "该课程校验或读取失败，请单独检查", "dry_run": base.Preview()}
		}
		result["xspjid"], result["course_name"], result["teacher_name"] = base.Record, course["course_name"], course["teacher_name"]
		results = append(results, result)
		if result["success"] == true {
			success++
		}
		if result["unknown"] == true {
			stopped = true
			break
		}
		if index < len(pending)-1 && delay > 0 {
			timer := time.NewTimer(time.Duration(delay * float64(time.Second)))
			select {
			case <-timer.C:
			case <-ctx.Done():
				timer.Stop()
				stopped = true
			}
			if stopped {
				break
			}
		}
	}
	refetches := []string{}
	if !base.Preview() && len(results) > 0 {
		refetches = []string{"evaluation-tasks", "evaluation-courses"}
	}
	writeJSON(response, 200, map[string]any{"results": results, "total": len(results), "pending_count": len(pending), "success_count": success, "dry_run": base.Preview(), "stopped": stopped, "remaining_count": len(pending) - len(results), "refetches": refetches})
}
