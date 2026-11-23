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

const jwxkAutomationTaskNamespace = "jwxk-automation-tasks"

func (api *API) jwxkAutomationTasks(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	if request.Method == "GET" {
		batch := request.URL.Query().Get("batch_code")
		if batch != "" && !(academic.JWXKSearch{Batch: batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
			writeJSON(response, 422, map[string]string{"detail": "轮次参数无效"})
			return
		}
		documents, err := api.database.ListDocuments(request.Context(), session, jwxkAutomationTaskNamespace)
		if err != nil {
			api.documentError(response, err)
			return
		}
		tasks := []academic.JWXKAutomationTask{}
		for _, document := range documents {
			var task academic.JWXKAutomationTask
			if json.Unmarshal(document.Payload, &task) != nil {
				api.documentError(response, store.ErrConflict)
				return
			}
			if task.Status != "cancelled" && (batch == "" || task.Batch == batch) {
				task.Revision = document.Revision
				tasks = append(tasks, task)
			}
		}
		writeJSON(response, 200, map[string]any{"tasks": tasks})
		return
	}
	input := academic.DefaultJWXKAutomationRequest()
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "自动任务参数、方案组或真实提交类型无效"})
		return
	}
	release, err := api.resources.acquireAccount(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	_, archive, err := api.readJWXKArchive(request.Context(), session, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	mode := academic.Text(archive["selection_type_code"])
	if mode == "" {
		round, err := api.readJWXKRound(request.Context(), session, input.Batch)
		if err != nil {
			api.documentError(response, err)
			return
		}
		mode = academic.Text(round["selection_type_code"])
	}
	if mode != "" && (input.Type == "weight_strategy" && mode != "04" || input.Type != "weight_strategy" && mode != "02") {
		writeJSON(response, 422, map[string]string{"detail": "任务类型与轮次模式不匹配"})
		return
	}
	task, err := api.createJWXKAutomationTask(request.Context(), session, input)
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, task)
}

func (api *API) createJWXKAutomationTask(ctx context.Context, session store.Session, input academic.JWXKAutomationRequest) (academic.JWXKAutomationTask, error) {
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return academic.JWXKAutomationTask{}, err
	}
	now := time.Now().UTC()
	task, err := academic.NewJWXKAutomationTask(input, hex.EncodeToString(identifier), now)
	if err != nil {
		return task, err
	}
	documents, err := api.database.ListDocuments(ctx, session, jwxkAutomationTaskNamespace)
	if err != nil {
		return task, err
	}
	changes := []store.DocumentChange{}
	active := 0
	for _, document := range documents {
		var previous academic.JWXKAutomationTask
		if json.Unmarshal(document.Payload, &previous) != nil {
			return task, store.ErrConflict
		}
		if previous.Status == "cancelled" {
			continue
		}
		active++
		if previous.Batch != input.Batch || previous.Type != input.Type || input.Type == "vacancy_swap" {
			continue
		}
		if !input.ReplaceExisting {
			return task, store.ErrConflict
		}
		if err := previous.Action("cancel", now); err != nil {
			return task, err
		}
		encoded, err := json.Marshal(previous)
		if err != nil {
			return task, err
		}
		changes = append(changes, store.DocumentChange{Namespace: jwxkAutomationTaskNamespace, Key: document.Key, Payload: encoded, Revision: document.Revision})
	}
	if active-len(changes) >= 100 {
		return task, store.ErrConflict
	}
	encoded, err := json.Marshal(task)
	if err != nil {
		return task, err
	}
	changes = append(changes, store.DocumentChange{Namespace: jwxkAutomationTaskNamespace, Key: task.ID, Payload: encoded})
	saved, err := api.database.SaveBatchForSession(ctx, session, changes)
	if err != nil {
		return task, err
	}
	task.Revision = saved[len(saved)-1].Revision
	return task, nil
}

func (api *API) jwxkSyncTaskTimes(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Batch string `json:"batch_code"`
		Start string `json:"start_at"`
		End   string `json:"end_at"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	start, startError := academic.ParseJWXKTaskTime(input.Start)
	end, endError := academic.ParseJWXKTaskTime(input.End)
	if !(academic.JWXKSearch{Batch: input.Batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() || len(input.Start) > 40 || len(input.End) > 40 || startError != nil || endError != nil || !end.After(start) {
		writeJSON(response, 422, map[string]string{"detail": "选课轮次时间无效"})
		return
	}
	documents, err := api.database.ListDocuments(request.Context(), session, jwxkAutomationTaskNamespace)
	if err != nil {
		api.documentError(response, err)
		return
	}
	changes, ids := []store.DocumentChange{}, []string{}
	for _, document := range documents {
		var task academic.JWXKAutomationTask
		if json.Unmarshal(document.Payload, &task) != nil {
			api.documentError(response, store.ErrConflict)
			return
		}
		if task.Batch != input.Batch {
			continue
		}
		changed, err := task.SyncTimes(input.Start, input.End, time.Now())
		if err != nil {
			api.documentError(response, err)
			return
		}
		if !changed {
			continue
		}
		payload, _ := json.Marshal(task)
		changes = append(changes, store.DocumentChange{Namespace: jwxkAutomationTaskNamespace, Key: document.Key, Payload: payload, Revision: document.Revision})
		ids = append(ids, task.ID)
	}
	document, archive, err := api.readJWXKArchive(request.Context(), session, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if archive != nil && (archive["begin_time"] != input.Start || archive["end_time"] != input.End) {
		archive["begin_time"], archive["end_time"], archive["updated_at"] = input.Start, input.End, time.Now().UTC().Format(time.RFC3339Nano)
		payload, _ := json.Marshal(archive)
		changes = append(changes, store.DocumentChange{Namespace: jwxkArchiveNamespace, Key: document.Key, Payload: payload, Revision: document.Revision})
	}
	if len(changes) > 0 {
		if _, err := api.database.SaveBatchForSession(request.Context(), session, changes); err != nil {
			api.documentError(response, err)
			return
		}
	}
	writeJSON(response, 200, map[string]any{"batch_code": input.Batch, "start_at": input.Start, "end_at": input.End, "changed_task_count": len(ids), "changed_task_ids": ids})
}
