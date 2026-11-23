package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const jwxkWeightApplicationNamespace = "weight-applications"

type weightApplyItem struct {
	Selection    academic.JWXKSelect    `json:"selection"`
	Removal      *academic.JWXKDeselect `json:"removal,omitempty"`
	Withdrawn    bool                   `json:"withdrawn"`
	Done         bool                   `json:"done"`
	WithdrawOnly bool                   `json:"withdraw_only"`
}

type weightApplyResult struct {
	Action string `json:"action"`
	Course string `json:"course_code"`
	Class  string `json:"class_id"`
	academic.JWXKMutationResult
}

type weightApplication struct {
	Hash     string            `json:"hash"`
	Term     string            `json:"term_code"`
	Phase    string            `json:"phase"`
	Items    []weightApplyItem `json:"items"`
	Revision int64             `json:"-"`
	Minimum  int               `json:"minimum"`
	Step     int               `json:"step"`
	Budget   int               `json:"budget"`
}

func weightApplyInputs(input academic.JWXKSavedPlan) ([]academic.JWXKSelect, string, error) {
	if !input.Valid() || len(input.Items) == 0 {
		return nil, "", &campus.Error{Code: "WEIGHT_PLAN_INVALID", Message: "投权方案参数无效"}
	}
	items, seen := []academic.JWXKSelect{}, map[string]bool{}
	for _, raw := range input.Items {
		weight, err := strconv.Atoi(academic.Text(raw["weight"]))
		item := academic.JWXKSelect{Batch: input.Batch, Class: academic.Text(raw["class_id"]), Course: strings.ToUpper(strings.TrimSpace(academic.Text(raw["course_code"]))), Scope: academic.Text(raw["teaching_class_type"]), Weight: &weight, ConfirmRisk: true}
		validated := item
		if weight == 0 && raw["withdraw_only"] == true {
			validated.Weight = nil
		}
		if err != nil || !validated.Valid() || seen[item.Course] || raw["withdraw_only"] == true && weight != 0 {
			return nil, "", &campus.Error{Code: "WEIGHT_PLAN_INVALID", Message: "课程、真实提交类型或权重无效，同一课程只能投放一次"}
		}
		seen[item.Course] = true
		items = append(items, item)
	}
	encoded, _ := json.Marshal([]any{input.Term, items})
	digest := sha256.Sum256(encoded)
	return items, hex.EncodeToString(digest[:]), nil
}

func (api *API) saveWeightApplication(ctx context.Context, session store.Session, batch string, application *weightApplication) error {
	payload, err := json.Marshal(application)
	if err != nil {
		return err
	}
	document, err := api.database.SaveForSession(ctx, session, jwxkWeightApplicationNamespace, batch, payload, application.Revision, false)
	if err == nil {
		application.Revision = document.Revision
	}
	return err
}

func (api *API) prepareWeightApplication(ctx context.Context, runtime *Runtime, session store.Session, mode string, input academic.JWXKSavedPlan, items []academic.JWXKSelect, hash string) (weightApplication, error) {
	application := weightApplication{Hash: hash, Term: input.Term, Phase: "withdraw", Items: []weightApplyItem{}}
	official, err := academic.FetchJWXKSelected(ctx, runtime, mode, input.Batch)
	if err != nil {
		return application, err
	}
	if official["complete"] != true || official["selection_type_code"] != "04" || official["term_code"] != input.Term {
		return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "当前权重轮次与学期或官方结果尚未完整核验"}
	}
	document, err := api.database.Read(ctx, session.Account, jwxkWeightApplicationNamespace, input.Batch)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return application, err
	}
	if err == nil {
		var previous weightApplication
		if document.Deleted || json.Unmarshal(document.Payload, &previous) != nil {
			return application, store.ErrConflict
		}
		previous.Revision = document.Revision
		if previous.Hash == hash && previous.Phase != "cancelled" {
			return previous, nil
		}
		if previous.Phase != "complete" && previous.Phase != "cancelled" {
			return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "上一份投权方案尚未完成，请先继续核验原方案，不能替换正在执行的目标"}
		}
		application.Revision = document.Revision
	}
	budget, err := academic.FetchJWXKWeightBudget(ctx, runtime, mode, input.Batch)
	if err != nil {
		return application, err
	}
	minimum, step := int(academic.Number(budget["minimum"])), int(academic.Number(budget["step"]))
	if minimum <= 0 || step <= 0 {
		return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "官方最低投权或步长无效"}
	}
	current := map[string]map[string]any{}
	for _, course := range jwxkArchiveRows(official["volunteered"]) {
		if course["current_batch_record"] != true {
			continue
		}
		code := strings.ToUpper(academic.Text(course["course_code"]))
		if current[code] != nil {
			return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "官方存在同课程重复志愿，请先核验"}
		}
		current[code] = course
	}
	total, available := 0, int(academic.Number(budget["remaining"]))
	operations, err := api.database.ListDocuments(ctx, session, "selection-attempts")
	if err != nil {
		return application, err
	}
	for _, item := range items {
		for _, operation := range operations {
			var removal store.SelectionAttempt
			if json.Unmarshal(operation.Payload, &removal) != nil {
				return application, store.ErrConflict
			}
			if removal.Batch == input.Batch && strings.EqualFold(removal.Course, item.Course) && (removal.State == "pending" || removal.State == "unknown") {
				return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "目标课程存在待核验退课，请先核验原操作"}
			}
		}
		for _, row := range jwxkArchiveRows(official["selected"]) {
			term := academic.Text(row["record_term_code"])
			if strings.EqualFold(academic.Text(row["course_code"]), item.Course) && current[item.Course] == nil && (term == "" || term == input.Term) {
				return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "目标课程已形成选课结果，不应重新投权"}
			}
		}
		if *item.Weight != 0 && (*item.Weight < minimum || *item.Weight%step != 0) {
			return application, &campus.Error{Code: "WEIGHT_PLAN_INVALID", Message: "投权金额不符合官方最低值或步长"}
		}
		total += *item.Weight
		attempt, err := api.database.SelectionAttempt(ctx, session, jwxkSelectionKey(item))
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return application, err
		}
		if err == nil && (attempt.State == "pending" || attempt.State == "unknown") {
			return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "目标课程存在待核验提交，请先核验原操作"}
		}
		target := weightApplyItem{Selection: item, WithdrawOnly: *item.Weight == 0}
		if row := current[item.Course]; row != nil {
			weight, err := strconv.Atoi(academic.Text(row["devoted_weight"]))
			if err != nil || weight < 0 || weight > 150 {
				return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "已有志愿权重无法核验"}
			}
			available += weight
			if academic.Text(row["class_id"]) == item.Class && weight == *item.Weight {
				target.Done = true
			} else {
				removal := academic.JWXKDeselect{Batch: input.Batch, Class: academic.Text(row["class_id"]), Source: academic.Text(row["selection_source"]), ConfirmRisk: true}
				if !removal.Valid() || removal.Source == "" || removal.Source == "yxkcyx" {
					return application, &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "已有志愿缺少可靠撤回来源"}
				}
				target.Removal = &removal
			}
		}
		application.Items = append(application.Items, target)
	}
	if total > available || total > 150 {
		return application, &campus.Error{Code: "WEIGHT_PLAN_INVALID", Message: "投放总额超过目标志愿撤回后的可用权重"}
	}
	application.Minimum, application.Step, application.Budget = minimum, step, available
	if err := api.saveWeightApplication(ctx, session, input.Batch, &application); err != nil {
		return application, err
	}
	return application, nil
}

func (api *API) pendingWeightApplication(ctx context.Context, session store.Session, batch, term string) (map[string]any, error) {
	document, err := api.database.Read(ctx, session.Account, jwxkWeightApplicationNamespace, batch)
	if errors.Is(err, store.ErrMissing) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var application weightApplication
	if document.Deleted || json.Unmarshal(document.Payload, &application) != nil {
		return nil, store.ErrConflict
	}
	if application.Phase == "complete" || application.Phase == "cancelled" || application.Term != term {
		return nil, nil
	}
	items, used := []map[string]any{}, 0
	for _, item := range application.Items {
		if item.Selection.Weight == nil {
			return nil, store.ErrConflict
		}
		used += *item.Selection.Weight
		items = append(items, map[string]any{"class_id": item.Selection.Class, "course_code": item.Selection.Course, "course_name": item.Selection.Course, "teaching_class_type": item.Selection.Scope, "weight": *item.Selection.Weight, "withdraw_only": item.WithdrawOnly, "reapply_required": item.Removal != nil, "classification": "待核验", "recommendation_reason": "恢复已确认提交的方案，已发送步骤只核验，不重复发送"})
	}
	return map[string]any{"recovery": true, "budget": application.Budget, "used": used, "minimum": application.Minimum, "step": application.Step, "items": items, "courses": items, "groups": []any{}, "warnings": []string{"上一份投权方案尚未完成。继续后先核验已发送步骤，再执行未发送步骤；本次不重新生成建议。"}}, nil
}

func (api *API) jwxkCancelWeightApplication(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Batch string `json:"batch_code"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !(academic.JWXKSearch{Batch: input.Batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "轮次参数无效"})
		return
	}
	release, err := api.resources.acquireAccount(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	document, err := api.database.Read(request.Context(), session.Account, jwxkWeightApplicationNamespace, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	var application weightApplication
	if document.Deleted || json.Unmarshal(document.Payload, &application) != nil {
		api.documentError(response, store.ErrConflict)
		return
	}
	for _, item := range application.Items {
		keys := []string{jwxkSelectionKey(item.Selection)}
		if item.Removal != nil {
			keys = append(keys, jwxkDeselectionKey(input.Batch, item.Removal.Class))
		}
		for _, key := range keys {
			attempt, err := api.database.SelectionAttempt(request.Context(), session, key)
			if errors.Is(err, store.ErrMissing) {
				continue
			}
			if err != nil {
				api.documentError(response, err)
				return
			}
			if attempt.State == "pending" || attempt.State == "unknown" {
				writeJSON(response, 409, map[string]string{"detail": "仍有结果未知的学校操作，请先继续核验，不能结束此方案"})
				return
			}
		}
	}
	application.Revision, application.Phase = document.Revision, "cancelled"
	if err := api.saveWeightApplication(request.Context(), session, input.Batch, &application); err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]bool{"success": true})
}

func (api *API) applyJWXKWeights(ctx context.Context, runtime *Runtime, session store.Session, mode string, input academic.JWXKSavedPlan) (map[string]any, error) {
	return api.advanceJWXKWeights(ctx, runtime, session, mode, input, 0, false)
}

func (api *API) advanceJWXKWeights(ctx context.Context, runtime *Runtime, session store.Session, mode string, input academic.JWXKSavedPlan, limit int, readOnly bool) (map[string]any, error) {
	items, hash, err := weightApplyInputs(input)
	if err != nil {
		return nil, err
	}
	application, err := api.prepareWeightApplication(ctx, runtime, session, mode, input, items, hash)
	if err != nil {
		return nil, err
	}
	results := []weightApplyResult{}
	steps := 0
	output := func(completed bool) map[string]any {
		return map[string]any{"results": results, "completed": completed, "phase": application.Phase, "yielded": !completed && limit > 0 && steps >= limit}
	}
	stopped := func(action string, item academic.JWXKSelect, class string, result academic.JWXKMutationResult, err error) bool {
		if err != nil {
			result.Success = false
			result.Message, result.Code = "当前步骤未完成，请继续核验原方案", "stopped"
			var failure *campus.Error
			if errors.As(err, &failure) {
				result.Message, result.Code = failure.Message, failure.Code
			}
		}
		results = append(results, weightApplyResult{Action: action, Course: item.Course, Class: class, JWXKMutationResult: result})
		return err != nil || !result.Success
	}
	if application.Phase == "withdraw" {
		for index := range application.Items {
			item := &application.Items[index]
			if item.Removal == nil || item.Withdrawn {
				continue
			}
			if readOnly {
				may, err := api.weightStepMayReconcile(ctx, session, jwxkDeselectionKey(input.Batch, item.Removal.Class))
				if err != nil || !may {
					return output(false), err
				}
			}
			result, err := api.deselectJWXKCourse(ctx, runtime, session, mode, *item.Removal)
			if stopped("weight_withdraw", item.Selection, item.Removal.Class, result, err) {
				return output(false), nil
			}
			item.Withdrawn = true
			if err := api.saveWeightApplication(ctx, session, input.Batch, &application); err != nil {
				return output(false), err
			}
			steps++
			if limit > 0 && steps >= limit {
				return output(false), nil
			}
		}
		application.Phase = "select"
		if err := api.saveWeightApplication(ctx, session, input.Batch, &application); err != nil {
			return output(false), err
		}
	}
	for index := range application.Items {
		item := &application.Items[index]
		if item.WithdrawOnly {
			verified, err := api.verifyWeightWithdrawal(ctx, runtime, mode, input.Batch, input.Term, item.Selection.Course)
			result := academic.JWXKMutationResult{State: "confirmed", Success: verified, Message: "已核验原志愿撤回，本次不再投放"}
			if !verified {
				result = jwxkSelectionUnknown()
			}
			if stopped("weight_withdraw", item.Selection, item.Selection.Class, result, err) {
				return output(false), nil
			}
			item.Done = true
			continue
		}
		action := "weight_add"
		if item.Removal != nil {
			action = "weight_reapply"
		}
		if item.Done {
			verified, term, err := academic.VerifyJWXKSelection(ctx, runtime, mode, item.Selection)
			result := academic.JWXKMutationResult{State: "confirmed", Success: verified && term == input.Term, Code: "already_selected", Message: "已核验现有投权，未重复提交"}
			if !result.Success {
				result = jwxkSelectionUnknown()
			}
			if stopped(action, item.Selection, item.Selection.Class, result, err) {
				return output(false), nil
			}
			continue
		}
		if readOnly {
			may, err := api.weightStepMayReconcile(ctx, session, jwxkSelectionKey(item.Selection))
			if err != nil || !may {
				return output(false), err
			}
		}
		result, err := api.selectJWXKCourse(ctx, runtime, session, mode, item.Selection)
		if stopped(action, item.Selection, item.Selection.Class, result, err) {
			return output(false), nil
		}
		item.Done = true
		if err := api.saveWeightApplication(ctx, session, input.Batch, &application); err != nil {
			return output(false), err
		}
		steps++
		if limit > 0 && steps >= limit {
			return output(false), nil
		}
	}
	application.Phase = "complete"
	if err := api.saveWeightApplication(ctx, session, input.Batch, &application); err != nil {
		return output(false), err
	}
	return output(true), nil
}

func (api *API) verifyWeightWithdrawal(ctx context.Context, runtime *Runtime, mode, batch, term, course string) (bool, error) {
	result, err := academic.FetchJWXKSelected(ctx, runtime, mode, batch)
	if err != nil {
		return false, err
	}
	if result["complete"] != true || result["selection_type_code"] != "04" || result["term_code"] != term {
		return false, nil
	}
	for _, row := range jwxkArchiveRows(result["volunteered"]) {
		if row["current_batch_record"] == true && strings.EqualFold(academic.Text(row["course_code"]), course) {
			return false, nil
		}
	}
	return true, nil
}

func (api *API) weightStepMayReconcile(ctx context.Context, session store.Session, key string) (bool, error) {
	attempt, err := api.database.SelectionAttempt(ctx, session, key)
	if errors.Is(err, store.ErrMissing) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return attempt.State == "pending" || attempt.State == "unknown" || attempt.State == "confirmed", nil
}

func (api *API) jwxkWeightApply(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.JWXKSavedPlan
	if !decodeJSON(response, request, &input) {
		return
	}
	if _, _, err := weightApplyInputs(input); err != nil {
		api.resourceError(response, err)
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	mode, _, err := api.jwxkPreference(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if mode == "follow" {
		_, mode = runtime.Client.Status()
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
	result, err := api.applyJWXKWeights(ctx, runtime, session, mode, input)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}
