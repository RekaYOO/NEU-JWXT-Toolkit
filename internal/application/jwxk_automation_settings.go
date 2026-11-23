package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const jwxkAutomationSettingsNamespace = "jwxk-automation-settings"

func (api *API) readJWXKAutomationSettings(ctx context.Context, session store.Session, batch string) (academic.JWXKAutomationSettings, store.Document, error) {
	settings := academic.DefaultJWXKAutomationSettings()
	generation, err := api.database.Generation(ctx, session.Account)
	if err != nil {
		return settings, store.Document{}, err
	}
	if generation != session.Generation {
		return settings, store.Document{}, store.ErrAccess
	}
	document, err := api.database.Read(ctx, session.Account, jwxkAutomationSettingsNamespace, batch)
	if errors.Is(err, store.ErrMissing) {
		return settings, store.Document{}, nil
	}
	if err != nil || document.Deleted {
		return settings, document, err
	}
	if json.Unmarshal(document.Payload, &settings) != nil || !settings.Valid() {
		return settings, document, store.ErrConflict
	}
	return settings, document, nil
}

func (api *API) jwxkAutomationSettings(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	batch := request.PathValue("batch_code")
	if !(academic.JWXKSearch{Batch: batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "轮次参数无效"})
		return
	}
	settings, document, err := api.readJWXKAutomationSettings(request.Context(), session, batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if request.Method == "PUT" {
		settings = academic.DefaultJWXKAutomationSettings()
		if !decodeJSON(response, request, &settings) {
			return
		}
		if !settings.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "自动化时间、间隔或通知阈值无效"})
			return
		}
		encoded, _ := json.Marshal(settings)
		if _, err := api.database.SaveForSession(request.Context(), session, jwxkAutomationSettingsNamespace, batch, encoded, document.Revision, false); err != nil {
			api.documentError(response, err)
			return
		}
	}
	encoded, _ := json.Marshal(settings)
	var payload map[string]any
	json.Unmarshal(encoded, &payload)
	payload["batch_code"], payload["batch_name"], payload["term_code"], payload["selection_type_code"] = batch, "", "", ""
	_, archive, err := api.readJWXKArchive(request.Context(), session, batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if archive != nil {
		for _, field := range []string{"batch_name", "term_code", "selection_type_code"} {
			payload[field] = academic.Text(archive[field])
		}
	} else {
		round, err := api.readJWXKRound(request.Context(), session, batch)
		if err != nil {
			api.documentError(response, err)
			return
		}
		payload["batch_name"], payload["term_code"], payload["selection_type_code"] = academic.Text(round["name"]), academic.Text(round["term_code"]), academic.Text(round["selection_type_code"])
	}
	mailConfig, err := api.readMailConfig(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	payload["smtp_configured"], payload["smtp_status"] = mailConfig.Valid(true), "请前往系统设置配置邮件"
	if mailConfig.Valid(true) {
		payload["smtp_status"] = "任务、轮次与人数提醒可用；未绑定任务的关注课程提醒仍在迁移"
	}
	writeJSON(response, 200, payload)
}
