package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) readTrackingState(ctx context.Context, session store.Session) (map[string]any, store.Document, error) {
	state := map[string]any{"stage": "disabled", "message": "成绩追踪未启用", "last_check_at": nil, "last_success_at": nil, "next_check_at": nil, "last_notification_at": nil, "last_error": nil}
	document, err := api.database.Read(ctx, session.Account, gradeTrackingNamespace, "state")
	if errors.Is(err, store.ErrMissing) {
		return state, store.Document{}, nil
	}
	if err != nil {
		return state, document, err
	}
	if !document.Deleted && json.Unmarshal(document.Payload, &state) != nil {
		return nil, document, store.ErrConflict
	}
	return state, document, nil
}

func (api *API) gradeTrackingStatus(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	config, _, err := api.readGradeTrackingConfig(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	state, _, err := api.readTrackingState(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	count, err := api.database.PendingMailCount(request.Context(), session, "grade_tracking")
	if err != nil {
		api.documentError(response, err)
		return
	}
	state["enabled"], state["pending_notifications"] = config.Enabled, count
	activation := academic.Text(state["activation_id"])
	delivered, err := api.database.MailDelivered(request.Context(), session, "grade_tracking", "initial:"+activation)
	if err != nil {
		api.documentError(response, err)
		return
	}
	state["initial_notification_pending"] = config.Enabled && activation != "" && !delivered
	lastDelivery, err := api.database.LastMailDelivery(request.Context(), session, "grade_tracking")
	if err != nil {
		api.documentError(response, err)
		return
	}
	if lastDelivery != "" {
		state["last_notification_at"] = lastDelivery
	}
	if !config.Enabled && activation != "" && int64(academic.Number(state["generation"])) != session.Generation {
		state["stage"], state["next_check_at"] = "disabled", nil
	}
	if _, active := api.trackingChecks.Load(session.Account); active {
		state["stage"], state["message"] = "checking", "正在检查最新成绩"
	}
	writeJSON(response, 200, state)
}

func (api *API) gradeTrackingCheck(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	if _, busy := api.trackingChecks.LoadOrStore(session.Account, true); busy {
		writeJSON(response, 409, map[string]string{"detail": "已有成绩检查正在进行"})
		return
	}
	defer api.trackingChecks.Delete(session.Account)
	result, err := api.checkTrackingScores(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "result": result})
}

func (api *API) checkTrackingScores(ctx context.Context, session store.Session) (result map[string]any, checkError error) {
	state, stateDocument, err := api.readTrackingState(ctx, session)
	if err != nil {
		return nil, err
	}
	config, _, err := api.readGradeTrackingConfig(ctx, session)
	if err != nil {
		return nil, err
	}
	state["stage"], state["message"], state["last_check_at"], state["last_error"] = "checking", "正在检查最新成绩", time.Now().UTC().Format(time.RFC3339Nano), nil
	encoded, _ := json.Marshal(state)
	stateDocument, err = api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", encoded, stateDocument.Revision, false)
	if err != nil {
		return nil, err
	}
	defer func() {
		if checkError == nil {
			return
		}
		kind := "UPSTREAM_RESPONSE_INVALID"
		var failure *campus.Error
		if errors.As(checkError, &failure) {
			kind = failure.Code
		}
		state["stage"], state["message"], state["last_error"] = "error", "成绩检查失败，已保留上次快照", kind
		if config.Enabled {
			state["next_check_at"] = time.Now().Add(time.Duration(config.IntervalMinutes) * time.Minute).UTC().Format(time.RFC3339Nano)
		}
		if strings.Contains(kind, "AUTH") {
			state["stage"], state["message"] = "waiting_login", "教务登录已失效，请恢复登录后再检查"
		}
		payload, _ := json.Marshal(state)
		persist, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		defer cancel()
		api.database.SaveForSession(persist, session, gradeTrackingNamespace, "state", payload, stateDocument.Revision, false)
	}()
	submitted, err := api.resources.Submit(ctx, session, "scores", "default", "tracking", true)
	if err != nil {
		return nil, err
	}
	if submitted["status"] == "throttled" {
		return nil, &campus.Error{Code: academic.Text(submitted["error_kind"]), Message: "成绩刷新正在退避，请稍后重试"}
	}
	identifier, _ := submitted["job_id"].(string)
	job, exists := api.resources.Job(session, identifier)
	if !exists {
		return nil, store.ErrMissing
	}
	select {
	case <-job.done:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	job, _ = api.resources.Job(session, identifier)
	if job.Status != "completed" {
		return nil, &campus.Error{Code: job.ErrorKind, Message: "成绩刷新未完成，已保留上次快照"}
	}
	entry, err := api.database.ReadCache(ctx, session, "scores", "default")
	if err != nil {
		return nil, err
	}
	var scores academic.Scores
	if json.Unmarshal(entry.Payload, &scores) != nil {
		return nil, store.ErrConflict
	}
	return api.commitTrackingScores(ctx, session, config, state, stateDocument, scores)
}

func (api *API) commitTrackingScores(ctx context.Context, session store.Session, config academic.GradeTrackingConfig, state map[string]any, stateDocument store.Document, scores academic.Scores) (map[string]any, error) {
	current, err := academic.NewGradeTrackingSnapshot(scores, time.Now())
	if err != nil {
		return nil, err
	}
	previousDocument, err := api.database.Read(ctx, session.Account, gradeTrackingNamespace, "snapshot")
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return nil, err
	}
	initial := errors.Is(err, store.ErrMissing) || previousDocument.Deleted
	var previous academic.GradeTrackingSnapshot
	if !initial && json.Unmarshal(previousDocument.Payload, &previous) != nil {
		return nil, store.ErrConflict
	}
	change := academic.GradeTrackingChange{Added: []academic.Score{}, Changed: []map[string]academic.Score{}, Removed: []academic.Score{}, DetailCourses: []academic.Score{}}
	if !initial {
		change = academic.CompareGradeTracking(previous, current)
	}
	policyScores := append(append([]academic.Score{}, current.Scores.Scores...), previous.Scores.Scores...)
	policy := api.gpaPolicy(ctx, session, policyScores)
	average, _, _ := gpaSummary(current.Scores.Scores, policy)
	state["stage"], state["message"], state["last_success_at"], state["last_error"] = "disabled", "成绩检查已完成；自动追踪尚未启用", current.Updated, nil
	state["course_count"], state["overall_gpa"], state["calculated_gpa"], state["gpa_policy"] = len(current.Scores.Scores), current.Scores.OverallGPA, average, policy
	state["last_change_count"] = len(change.Added) + len(change.Changed) + len(change.Removed)
	state["last_seen_revision"], state["next_check_at"] = current.Revision, nil
	if config.Enabled {
		state["stage"], state["message"] = "monitoring", "成绩检查已完成"
		next, _ := config.NextCheck(time.Now(), false)
		state["next_check_at"] = next.UTC().Format(time.RFC3339Nano)
	}
	var mail *store.MailDraft
	activation := academic.Text(state["activation_id"])
	activationInitial := activation != "" && state["activation_queued"] != activation
	if config.Enabled && (activationInitial || change.HasChanges()) {
		message := trackingMail(current, previous, change, policy, activation, activationInitial)
		if !initial && !activationInitial {
			appendTrackingDetails(&message, api.trackingDetails(ctx, session, change.DetailCourses))
		}
		payload, err := json.Marshal(message)
		if err != nil || !message.Valid() {
			return nil, store.ErrConflict
		}
		dedupe := "revision:" + activation + ":" + current.Revision
		if activationInitial {
			dedupe = "initial:" + activation
			state["activation_queued"] = activation
		}
		mail = &store.MailDraft{Source: "grade_tracking", Dedupe: dedupe, Payload: payload}
	}
	statePayload, _ := json.Marshal(state)
	snapshotPayload, _ := json.Marshal(current)
	if _, err := api.database.SaveBatchWithMail(ctx, session, []store.DocumentChange{{Namespace: gradeTrackingNamespace, Key: "state", Payload: statePayload, Revision: stateDocument.Revision}, {Namespace: gradeTrackingNamespace, Key: "snapshot", Payload: snapshotPayload, Revision: previousDocument.Revision}}, mail); err != nil {
		return nil, err
	}
	if mail != nil {
		select {
		case api.accounts.mailWake <- struct{}{}:
		default:
		}
	}
	state["revision"], state["initial"], state["additions"], state["changes"], state["removals"] = current.Revision, initial, change.Added, change.Changed, change.Removed
	return state, nil
}
