package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func evaluationOperationKey(cycle, task, record string) string {
	encoded, _ := json.Marshal([]string{cycle, task, record})
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func evaluationResult(state string, preview bool) map[string]any {
	result := map[string]any{"success": state == "confirmed", "dry_run": preview, "state": state, "refetches": []string{}}
	switch state {
	case "confirmed":
		result["message"] = "学校已确认评教提交"
		if !preview {
			result["refetches"] = []string{"evaluation-tasks", "evaluation-courses"}
		}
	case "rejected":
		result["message"] = "学校拒绝评分：评价选项不能全部相同"
	case "not_sent":
		result["message"] = "评教连接未建立，评价未发送；请恢复连接后重试"
	default:
		result["message"] = "提交结果尚未确认，请刷新核对；不会自动重复提交"
		result["unknown"] = true
		result["refetches"] = []string{"evaluation-tasks", "evaluation-courses"}
	}
	return result
}

func (api *API) evaluateOne(ctx context.Context, runtime *Runtime, session store.Session, input academic.EvaluationInput) (map[string]any, error) {
	prepared, err := academic.PrepareEvaluation(ctx, runtime, session.Account, input)
	if err != nil {
		return nil, err
	}
	key := evaluationOperationKey(prepared.Cycle, input.Task, input.Record)
	if prepared.AlreadySubmitted {
		if attempt, err := api.database.EvaluationAttempt(ctx, session, key); err == nil {
			if err := api.database.FinishEvaluation(ctx, session, key, attempt.Attempt, "confirmed"); err != nil {
				return nil, err
			}
		}
		result := evaluationResult("confirmed", input.Preview())
		result["skipped"] = true
		result["message"] = "该课程已评价，不重复提交"
		return result, nil
	}
	if input.Preview() {
		return map[string]any{"success": true, "dry_run": true, "message": "安全模式预览完成，未向学校提交", "preview": prepared.Summary, "refetches": []string{}}, nil
	}
	attempt, err := api.database.BeginEvaluation(ctx, session, key)
	if errors.Is(err, store.ErrConflict) {
		result := evaluationResult("unknown", false)
		if attempt.State == "confirmed" {
			result = evaluationResult("confirmed", false)
			result["skipped"] = true
		}
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	if ctx.Err() != nil || runtime.Context.Err() != nil {
		return evaluationResult("unknown", false), nil
	}
	state := academic.SendEvaluation(ctx, runtime, prepared.Payload)
	persistContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	if err := api.database.FinishEvaluation(persistContext, session, key, attempt.Attempt, state); err != nil {
		return evaluationResult("unknown", false), nil
	}
	result := evaluationResult(state, false)
	result["course_name"], result["teacher_name"] = academic.Text(prepared.Course["kcmc"]), academic.Text(prepared.Course["jgxm"])
	if state == "confirmed" {
		result["summary"] = prepared.Summary
	}
	return result, nil
}

func (api *API) evaluationSubmit(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var input academic.EvaluationInput
	if !decodeJSON(response, request, &input) {
		return
	}
	if err := input.Validate(); err != nil {
		writeJSON(response, 422, map[string]string{"detail": err.Error()})
		return
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
	result, err := api.evaluateOne(ctx, runtime, session, input)
	if err != nil {
		api.evaluationError(response, err)
		return
	}
	writeJSON(response, 200, result)
}

func (api *API) evaluationError(response http.ResponseWriter, err error) {
	var failure *campus.Error
	if errors.As(err, &failure) {
		status := 0
		if failure.Code == "INVALID_REQUEST" {
			status = 422
		}
		if failure.Code == "EVALUATION_COURSE_MISSING" {
			status = 404
		}
		if status != 0 {
			writeJSON(response, status, map[string]string{"detail": failure.Message, "error_code": failure.Code})
			return
		}
	}
	api.resourceError(response, err)
}
