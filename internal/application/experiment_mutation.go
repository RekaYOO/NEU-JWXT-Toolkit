package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type experimentOperation struct {
	Input   academic.ExperimentMutation `json:"input"`
	Action  string                      `json:"action"`
	State   string                      `json:"state"`
	Attempt int64                       `json:"attempt"`
}

func experimentOperationKey(input academic.ExperimentMutation) string {
	payload, _ := json.Marshal([]string{input.Term, input.Task, input.Project})
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:])
}

func experimentOutcome(state, message string) map[string]any {
	code := "-1"
	if state == "confirmed" {
		code = "0"
	}
	return map[string]any{"code": code, "msg": message, "state": state, "success": state == "confirmed"}
}

func (api *API) experimentMutation(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.ExperimentMutation
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "学期、任务、项目和班次均须为有效标识"})
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	action := "select"
	if strings.HasSuffix(request.URL.Path, "/deselect") {
		action = "deselect"
	}
	var result map[string]any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		if strings.HasSuffix(request.URL.Path, "/verify") {
			result, err = api.verifyExperimentOperation(request.Context(), runtime, session, input)
			return err
		}
		result, err = api.mutateExperiment(request.Context(), runtime, session, input, action)
		return err
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}

func (api *API) verifyExperimentOperation(ctx context.Context, runtime *Runtime, session store.Session, input academic.ExperimentMutation) (map[string]any, error) {
	key := experimentOperationKey(input)
	document, err := api.database.Read(ctx, session.Account, "experiment-operations", key)
	if errors.Is(err, store.ErrMissing) {
		return experimentOutcome("not_sent", "没有待核验提交记录，请刷新后选择班次"), nil
	}
	if err != nil {
		return nil, err
	}
	var operation experimentOperation
	if document.Deleted || json.Unmarshal(document.Payload, &operation) != nil {
		return nil, store.ErrConflict
	}
	if operation.Input.Round != input.Round {
		return experimentOutcome("unknown", "该项目已有其他班次的操作，请刷新项目状态后核对"), nil
	}
	if operation.State == "rejected" || operation.State == "not_sent" {
		return experimentOutcome("not_sent", "上次操作未被学校接受，可刷新后重新操作"), nil
	}
	payload, err := academic.FetchExperimentCourses(ctx, runtime, input.Term)
	if err != nil {
		return nil, err
	}
	if !academic.ExperimentMutationObserved(payload, operation.Input, operation.Action) {
		return experimentOutcome("unknown", "官方结果尚未确认，请稍后再次核验；本次没有重复提交"), nil
	}
	operation.State = "confirmed"
	encoded, _ := json.Marshal(operation)
	if _, err := api.database.SaveForSession(ctx, session, "experiment-operations", key, encoded, document.Revision, false); err != nil {
		return nil, err
	}
	result := experimentOutcome("confirmed", "已核验官方结果，操作已确认")
	result["cache_updated"] = api.invalidateExperimentMutation(ctx, runtime, session, input.Term)
	return result, nil
}

func (api *API) mutateExperiment(ctx context.Context, runtime *Runtime, session store.Session, input academic.ExperimentMutation, action string) (map[string]any, error) {
	unknown := experimentOutcome("unknown", "该项目已有待核验的操作，请刷新官方结果后再次核验；不会重复提交")
	key := experimentOperationKey(input)
	document, err := api.database.Read(ctx, session.Account, "experiment-operations", key)
	var previous experimentOperation
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return nil, err
	}
	if err == nil && (document.Deleted || json.Unmarshal(document.Payload, &previous) != nil) {
		return nil, store.ErrConflict
	}
	payload, err := academic.FetchExperimentCourses(ctx, runtime, input.Term)
	if err != nil {
		return nil, err
	}
	if previous.State == "pending" || previous.State == "unknown" || previous.State == "confirmed" {
		observed := academic.ExperimentMutationObserved(payload, previous.Input, previous.Action)
		if !observed && previous.State != "confirmed" {
			return unknown, nil
		}
		if previous.State != "confirmed" {
			previous.State = "confirmed"
			encoded, _ := json.Marshal(previous)
			document, err = api.database.SaveForSession(ctx, session, "experiment-operations", key, encoded, document.Revision, false)
			if err != nil {
				return unknown, err
			}
			api.invalidateExperimentMutation(ctx, runtime, session, previous.Input.Term)
		}
		if observed && previous.Action == action && previous.Input.Round == input.Round {
			return experimentOutcome("confirmed", "已核验官方结果，未重复提交"), nil
		}
	}
	if academic.ExperimentMutationObserved(payload, input, action) {
		return experimentOutcome("confirmed", "官方结果已符合本次操作，无需重复提交"), nil
	}
	if action == "select" {
		course, selected, found := academic.ExperimentProjectState(payload, input)
		if !found || selected != "" {
			return experimentOutcome("not_sent", "该实验项目当前不可选，或已选择其他班次；请刷新后核对"), nil
		}
		query := academic.ExperimentQuery{Term: input.Term, Task: input.Task, Course: course, Project: input.Project}
		rounds, err := academic.FetchExperimentRounds(ctx, runtime, query)
		if err != nil {
			return nil, err
		}
		baseline, available, stale := api.experimentBaseline(ctx, session, input.Term)
		allowed := false
		for _, round := range rounds {
			if academic.Text(round["wid"]) != input.Round {
				continue
			}
			academic.ExperimentRoundAvailability(round, query, baseline, available, stale, time.Now())
			if round["selected"] == true {
				return experimentOutcome("not_sent", "该班次已选中但项目结果尚未同步，请刷新核验"), nil
			}
			if round["can_select"] != true {
				return experimentOutcome("not_sent", academic.Text(round["disabled_reason"])), nil
			}
			allowed = true
		}
		if !allowed {
			return experimentOutcome("not_sent", "目标实验班已不可见，请刷新班次"), nil
		}
	}
	operation := experimentOperation{Input: input, Action: action, State: "pending", Attempt: previous.Attempt + 1}
	encoded, _ := json.Marshal(operation)
	document, err = api.database.SaveForSession(ctx, session, "experiment-operations", key, encoded, document.Revision, false)
	if err != nil {
		return unknown, err
	}
	state, message := academic.SendExperimentMutation(ctx, runtime, input, action)
	if state == "unknown" {
		if latest, verifyErr := academic.FetchExperimentCourses(ctx, runtime, input.Term); verifyErr == nil && academic.ExperimentMutationObserved(latest, input, action) {
			state, message = "confirmed", "已核验官方结果，操作成功"
		}
	}
	persist, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	operation.State = state
	encoded, _ = json.Marshal(operation)
	if _, err := api.database.SaveForSession(persist, session, "experiment-operations", key, encoded, document.Revision, false); err != nil {
		return unknown, nil
	}
	result := experimentOutcome(state, message)
	if state == "confirmed" {
		result["cache_updated"] = api.invalidateExperimentMutation(persist, runtime, session, input.Term)
	}
	return result, nil
}

func (api *API) invalidateExperimentMutation(ctx context.Context, runtime *Runtime, session store.Session, term string) bool {
	updated := true
	for _, resource := range []string{"academic-report", "experiment-courses", "personal-timetable"} {
		variant := "default"
		if resource == "personal-timetable" {
			variant = "term:" + term
		}
		if err := api.database.InvalidateCache(ctx, session, resource, variant); err != nil {
			updated = false
		}
	}
	if terms, err := academic.FetchTimetableTerms(ctx, runtime); err == nil && academic.Text(terms["current"]) == term {
		if _, err := api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+term, "experiment.mutation", true); err != nil {
			updated = false
		}
	}
	return updated
}
