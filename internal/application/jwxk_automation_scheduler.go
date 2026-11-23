package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) startAutomationScheduler() {
	api.accounts.mutex.Lock()
	defer api.accounts.mutex.Unlock()
	if api.accounts.context.Err() != nil {
		return
	}
	api.accounts.automationTick = api.scheduleAutomationTasks
	api.accounts.automationOnce.Do(func() {
		api.accounts.workers.Add(1)
		go func() {
			defer api.accounts.workers.Done()
			ticker := time.NewTicker(time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-api.accounts.context.Done():
					return
				case <-ticker.C:
					api.accounts.mutex.RLock()
					callback := api.accounts.automationTick
					api.accounts.mutex.RUnlock()
					if callback != nil {
						callback()
					}
				}
			}
		}()
	})
}

func (api *API) scheduleAutomationTasks() {
	sessions := []store.Session{}
	activeAccounts := map[string]bool{}
	api.accounts.mutex.RLock()
	for account, runtime := range api.accounts.runtimes {
		active, _ := runtime.Client.Status()
		if service, ok := runtime.Client.(interface{ TaskServiceAvailable(string) bool }); ok {
			active = active || service.TaskServiceAvailable("jwxk")
		}
		if runtime.Context.Err() == nil {
			sessions = append(sessions, store.Session{Account: account, Generation: runtime.Generation})
			activeAccounts[account] = active
		}
	}
	api.accounts.mutex.RUnlock()
	for _, session := range sessions {
		api.scheduleAuthRecovery(session)
		api.scheduleGradeTracking(api.accounts.context, session)
		notify := true
		if previous, exists := api.mailScanTimes.Load(session.Account); exists && time.Since(previous.(time.Time)) < 20*time.Second {
			notify = false
		}
		if notify {
			api.mailScanTimes.Store(session.Account, time.Now())
		}
		documents, err := api.database.ListDocuments(api.accounts.context, session, jwxkAutomationTaskNamespace)
		if err != nil {
			continue
		}
		for _, document := range documents {
			var task academic.JWXKAutomationTask
			if json.Unmarshal(document.Payload, &task) != nil {
				continue
			}
			task.Revision = document.Revision
			if notify {
				api.notifyJWXKTask(api.accounts.context, session, task)
			}
			if !activeAccounts[session.Account] || task.Desired != "running" || task.Status != "running" && task.Status != "waiting" {
				continue
			}
			if next, err := time.Parse(time.RFC3339Nano, task.NextAttempt); err == nil && time.Now().Before(next) {
				continue
			}
			id := task.ID
			api.resources.startDocumentWork(session, "jwxk-automation", id, func(ctx context.Context, runtime *Runtime) error {
				return api.resources.documentWorkStep(ctx, session, func() error { return api.tickJWXKSelectionTask(ctx, runtime, session, id) })
			})
		}
	}
}

func (api *API) saveAutomationTask(ctx context.Context, session store.Session, task *academic.JWXKAutomationTask) error {
	task.Updated = time.Now().UTC().Format(time.RFC3339Nano)
	encoded, err := json.Marshal(task)
	if err != nil {
		return err
	}
	document, err := api.database.SaveForSession(ctx, session, jwxkAutomationTaskNamespace, task.ID, encoded, task.Revision, false)
	if err == nil {
		task.Revision = document.Revision
	}
	return err
}

func (api *API) jwxkTaskAction(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	action := request.PathValue("action")
	if action != "start" && action != "pause" && action != "cancel" && action != "check_now" {
		writeJSON(response, 404, map[string]string{"detail": "未知任务操作"})
		return
	}
	var input struct {
		ID string `json:"task_id"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !(academic.JWXKSearch{Batch: input.ID, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "任务标识无效"})
		return
	}
	release, err := api.resources.acquireAccount(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	document, err := api.database.Read(request.Context(), session.Account, jwxkAutomationTaskNamespace, input.ID)
	if err != nil {
		api.documentError(response, err)
		return
	}
	var task academic.JWXKAutomationTask
	if document.Deleted || json.Unmarshal(document.Payload, &task) != nil {
		api.documentError(response, store.ErrConflict)
		return
	}
	now := time.Now().UTC()
	if err := task.Action(action, now); err != nil {
		writeJSON(response, 409, map[string]string{"detail": err.Error()})
		return
	}
	if action == "check_now" {
		next := now
		interval := task.PollInterval()
		if task.Type == "weight_strategy" {
			interval = 15 * time.Second
		}
		if last, err := time.Parse(time.RFC3339Nano, task.LastAttempt); err == nil && last.Add(interval).After(next) {
			next = last.Add(interval)
		}
		if academic.Text(task.Execution["last_error_type"]) != "" {
			if current, err := time.Parse(time.RFC3339Nano, task.NextAttempt); err == nil && current.After(next) {
				next = current
			}
		}
		task.NextAttempt = next.Format(time.RFC3339Nano)
	}
	task.Revision = document.Revision
	if err := api.saveAutomationTask(request.Context(), session, &task); err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, task)
}

func selectionTaskMayReconcile(ctx context.Context, api *API, session store.Session, input academic.JWXKSelect) (bool, error) {
	attempt, err := api.database.SelectionAttempt(ctx, session, jwxkSelectionKey(input))
	if errors.Is(err, store.ErrMissing) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return attempt.State == "pending" || attempt.State == "unknown" || attempt.State == "confirmed", nil
}
