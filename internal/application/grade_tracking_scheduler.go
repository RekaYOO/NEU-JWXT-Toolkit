package application

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) gradeTrackingEnabled(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Enabled *bool `json:"enabled"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Enabled == nil {
		writeJSON(response, 422, map[string]string{"detail": "必须明确指定追踪开关"})
		return
	}
	config, document, err := api.readGradeTrackingConfig(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if config.Enabled == *input.Enabled {
		writeJSON(response, 200, map[string]any{"success": true, "config": config})
		return
	}
	state, stateDocument, err := api.readTrackingState(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if *input.Enabled {
		mail, err := api.readMailConfig(request.Context(), session)
		if err != nil {
			api.documentError(response, err)
			return
		}
		if !mail.Valid(true) {
			writeJSON(response, 400, map[string]string{"detail": "请先在系统设置完成邮件配置"})
			return
		}
		identifier := make([]byte, 16)
		if _, err := rand.Read(identifier); err != nil {
			api.documentError(response, err)
			return
		}
		state["activation_id"], state["generation"] = hex.EncodeToString(identifier), session.Generation
		state["stage"], state["message"], state["next_check_at"], state["last_error"] = "scheduled", "成绩追踪已启用，正在准备初始同步", time.Now().UTC().Format(time.RFC3339Nano), nil
	} else {
		state["activation_id"], state["stage"], state["message"], state["next_check_at"] = "", "disabled", "成绩追踪已关闭", nil
	}
	config.Enabled = *input.Enabled
	payload, _ := json.Marshal(config)
	statePayload, _ := json.Marshal(state)
	if _, err := api.database.SaveBatchForSession(request.Context(), session, []store.DocumentChange{{Namespace: gradeTrackingNamespace, Key: "config", Payload: payload, Revision: document.Revision}, {Namespace: gradeTrackingNamespace, Key: "state", Payload: statePayload, Revision: stateDocument.Revision}}); err != nil {
		api.documentError(response, err)
		return
	}
	if !config.Enabled {
		api.database.DiscardMail(request.Context(), session, "grade_tracking", "")
	}
	writeJSON(response, 200, map[string]any{"success": true, "config": config})
}

func (api *API) scheduleGradeTracking(ctx context.Context, session store.Session) {
	config, _, err := api.readGradeTrackingConfig(ctx, session)
	if err != nil || !config.Enabled {
		return
	}
	state, _, err := api.readTrackingState(ctx, session)
	if err != nil {
		return
	}
	api.notifyTrackingAuth(ctx, session, state)
	now := time.Now()
	if next, err := time.Parse(time.RFC3339Nano, academic.Text(state["next_check_at"])); err == nil && now.Before(next) {
		return
	}
	initial := academic.Text(state["activation_id"]) != "" && state["activation_queued"] != state["activation_id"]
	if !initial && !config.WithinWindow(now) {
		return
	}
	api.resources.startDocumentWork(session, "grade-tracking", "default", func(ctx context.Context, runtime *Runtime) error {
		if _, busy := api.trackingChecks.LoadOrStore(session.Account, true); busy {
			return nil
		}
		defer api.trackingChecks.Delete(session.Account)
		current, _, err := api.readGradeTrackingConfig(ctx, session)
		if err != nil || !current.Enabled {
			return err
		}
		latest, _, err := api.readTrackingState(ctx, session)
		if err != nil {
			return err
		}
		if next, err := time.Parse(time.RFC3339Nano, academic.Text(latest["next_check_at"])); err == nil && time.Now().Before(next) {
			return nil
		}
		_, err = api.checkTrackingScores(ctx, session)
		return err
	})
}

func (api *API) wakeGradeTracking(session store.Session) {
	ctx := api.accounts.context
	state, document, err := api.readTrackingState(ctx, session)
	if err != nil {
		return
	}
	config, _, err := api.readGradeTrackingConfig(ctx, session)
	if err != nil || !config.Enabled {
		return
	}
	state["next_check_at"], state["stage"], state["message"], state["last_error"] = time.Now().UTC().Format(time.RFC3339Nano), "scheduled", "登录已恢复，等待检查最新成绩", nil
	payload, _ := json.Marshal(state)
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", payload, document.Revision, false)
}
