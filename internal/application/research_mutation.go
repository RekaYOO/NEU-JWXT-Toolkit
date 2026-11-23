package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type researchOperation struct {
	Batch   string `json:"batch_id"`
	Topic   string `json:"topic_id"`
	Action  string `json:"action"`
	State   string `json:"state"`
	Attempt int64  `json:"attempt"`
}

func researchOutcome(state, message string) map[string]any {
	return map[string]any{"state": state, "message": message, "success": state == "confirmed"}
}

func researchOperationObserved(rows []map[string]any, topic, action string) bool {
	for _, row := range rows {
		if academic.Text(row["topic_id"]) == topic {
			registered := row["is_registered"] == true
			return action == "enroll" && registered || action == "cancel" && !registered
		}
	}
	return false
}

func (api *API) researchMutation(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	action := "enroll"
	if strings.HasSuffix(request.URL.Path, "/cancel") {
		action = "cancel"
	}
	if strings.HasSuffix(request.URL.Path, "/verify") {
		action = "verify"
	}
	var input academic.ResearchEnrollment
	if action == "enroll" {
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "报名参数无效"})
			return
		}
	} else {
		var identity struct {
			Topic string `json:"topic_id"`
		}
		if !decodeJSON(response, request, &identity) {
			return
		}
		input.Topic = identity.Topic
		if !academic.ValidEvaluationID(input.Topic) {
			writeJSON(response, 422, map[string]string{"detail": "课题编号无效"})
			return
		}
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var result map[string]any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		result, err = api.mutateResearch(request.Context(), runtime, session, input, action)
		return err
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}

func (api *API) mutateResearch(ctx context.Context, runtime *Runtime, session store.Session, input academic.ResearchEnrollment, action string) (map[string]any, error) {
	unknown := researchOutcome("unknown", "该课题已有待核验操作，请核验官方报名记录；不会重复提交")
	key := researchFavoriteKey("operation", input.Topic)
	document, err := api.database.Read(ctx, session.Account, "research-operations", key)
	var previous researchOperation
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return nil, err
	}
	if err == nil && (document.Deleted || json.Unmarshal(document.Payload, &previous) != nil) {
		return nil, store.ErrConflict
	}
	if previous.State == "pending" || previous.State == "unknown" || previous.State == "confirmed" {
		rows, err := academic.FetchResearchTopics(ctx, runtime, previous.Batch)
		if err != nil {
			return nil, err
		}
		observed := researchOperationObserved(rows, previous.Topic, previous.Action)
		if !observed && (previous.State != "confirmed" || action == "verify") {
			return unknown, nil
		}
		if observed {
			if previous.State != "confirmed" {
				previous.State = "confirmed"
				encoded, _ := json.Marshal(previous)
				document, err = api.database.SaveForSession(ctx, session, "research-operations", key, encoded, document.Revision, false)
				if err != nil {
					return nil, err
				}
				api.invalidateResearchMutation(ctx, runtime, session)
			}
			if action == "verify" || action == previous.Action && (action == "cancel" || input.Batch == previous.Batch) {
				return researchOutcome("confirmed", "已核验官方报名记录，未重复提交"), nil
			}
		}
	}
	if action == "verify" {
		return researchOutcome("not_sent", "没有待核验提交，刷新课题后可重新操作"), nil
	}
	batch, err := academic.FetchResearchBatch(ctx, runtime)
	if err != nil {
		return nil, err
	}
	batchID := academic.Text(batch["batch_id"])
	if action == "enroll" && input.Batch != batchID {
		return researchOutcome("not_sent", "报名批次已变化，请刷新后重试"), nil
	}
	rows, err := academic.FetchResearchTopics(ctx, runtime, batchID)
	if err != nil {
		return nil, err
	}
	var topic map[string]any
	for _, row := range rows {
		if academic.Text(row["topic_id"]) == input.Topic {
			topic = row
			break
		}
	}
	if topic == nil {
		return researchOutcome("not_sent", "课题已不可见，请刷新后核对"), nil
	}
	if researchOperationObserved(rows, input.Topic, action) {
		return researchOutcome("confirmed", "官方状态已符合本次操作，无需重复提交"), nil
	}
	var eligibility map[string]any
	if action == "enroll" {
		if topic["can_enroll"] != true {
			return researchOutcome("not_sent", "课题当前不可报名，请刷新名额和报名状态"), nil
		}
		eligibility, err = academic.FetchResearchEligibility(ctx, runtime, batchID)
		if err != nil {
			return nil, err
		}
		if eligibility["available"] != true || academic.Text(eligibility["gpa"]) == "" || academic.Text(eligibility["major_rank"]) == "" {
			return researchOutcome("not_sent", "学校资格信息不完整，未提交报名"), nil
		}
	} else if topic["can_cancel"] != true {
		return researchOutcome("not_sent", "当前报名状态不允许取消，请核对学校结果"), nil
	}
	operation := researchOperation{Batch: batchID, Topic: input.Topic, Action: action, State: "pending", Attempt: previous.Attempt + 1}
	encoded, _ := json.Marshal(operation)
	document, err = api.database.SaveForSession(ctx, session, "research-operations", key, encoded, document.Revision, false)
	if err != nil {
		return nil, err
	}
	var state, message string
	if action == "enroll" {
		state, message = academic.SendResearchEnrollment(ctx, runtime, input, eligibility)
	} else {
		state, message = academic.SendResearchCancellation(ctx, runtime, input.Topic)
	}
	if state == "unknown" {
		if latest, err := academic.FetchResearchTopics(ctx, runtime, batchID); err == nil && researchOperationObserved(latest, input.Topic, action) {
			state, message = "confirmed", "已核验官方报名记录，操作成功"
		}
	}
	persist, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	operation.State = state
	encoded, _ = json.Marshal(operation)
	if _, err := api.database.SaveForSession(persist, session, "research-operations", key, encoded, document.Revision, false); err != nil {
		return unknown, nil
	}
	result := researchOutcome(state, message)
	if state == "confirmed" {
		result["cache_updated"] = api.invalidateResearchMutation(persist, runtime, session)
	}
	return result, nil
}

func (api *API) invalidateResearchMutation(ctx context.Context, runtime *Runtime, session store.Session) bool {
	if err := api.database.InvalidateCache(ctx, session, "research-training", "default"); err != nil {
		return false
	}
	_, err := api.resources.Submit(runtime.Context, session, "research-training", "default", "research.mutation", true)
	return err == nil
}
