package application

import (
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) jwxkPlanRead(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
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
		writeJSON(response, 422, map[string]string{"detail": "方案轮次无效"})
		return
	}
	plan := academic.JWXKSavedPlan{Batch: input.Batch}
	document, err := api.database.Read(request.Context(), session.Account, "selection-plans", input.Batch)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	if err == nil && !document.Deleted {
		if json.Unmarshal(document.Payload, &plan) != nil || plan.Batch != input.Batch {
			api.documentError(response, store.ErrConflict)
			return
		}
	}
	plan.Revision = document.Revision
	batch, err := api.readJWXKRound(request.Context(), session, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if plan.Term == "" {
		plan.Term = academic.Text(batch["term_code"])
	}
	writeJSON(response, 200, struct {
		academic.JWXKSavedPlan
		BatchSnapshot map[string]any `json:"batch"`
	}{academic.NormalizeJWXKPlan(plan), batch})
}

func (api *API) jwxkPlanSave(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.JWXKSavedPlan
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "方案参数无效"})
		return
	}
	input = academic.NormalizeJWXKPlan(input)
	encoded, err := json.Marshal(input)
	if err != nil {
		api.documentError(response, err)
		return
	}
	changes := []store.DocumentChange{{Namespace: "selection-plans", Key: input.Batch, Payload: encoded, Revision: input.Revision}}
	tasks, err := api.database.ListDocuments(request.Context(), session, jwxkAutomationTaskNamespace)
	if err != nil {
		api.documentError(response, err)
		return
	}
	for _, document := range tasks {
		var task academic.JWXKAutomationTask
		if json.Unmarshal(document.Payload, &task) != nil {
			api.documentError(response, store.ErrConflict)
			return
		}
		if !task.SyncPlan(input, time.Now().UTC()) {
			continue
		}
		payload, err := json.Marshal(task)
		if err != nil {
			api.documentError(response, err)
			return
		}
		changes = append(changes, store.DocumentChange{Namespace: jwxkAutomationTaskNamespace, Key: document.Key, Payload: payload, Revision: document.Revision})
	}
	documents, err := api.database.SaveBatchForSession(request.Context(), session, changes)
	if err != nil {
		api.documentError(response, err)
		return
	}
	input.Revision = documents[0].Revision
	writeJSON(response, 200, input)
}
