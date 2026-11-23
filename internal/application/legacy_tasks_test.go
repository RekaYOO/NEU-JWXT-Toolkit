package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func legacyTaskFixture(t *testing.T, extra map[string]any) json.RawMessage {
	t.Helper()
	var task map[string]any
	if err := json.Unmarshal([]byte(strings.ReplaceAll(automationTaskBody, "FANKC", "XGKC")), &task); err != nil {
		t.Fatal(err)
	}
	task["task_id"], task["account"], task["status"], task["desired_state"] = "legacy-task", "student-a", "running", "running"
	for key, value := range extra {
		task[key] = value
	}
	payload, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func TestLegacyTasksImportPausedAndVerifyWithoutResubmission(t *testing.T) {
	client := &selectionTaskClient{}
	api, handler, cookie, session, _ := selectionTaskSetup(t, client)
	root := t.TempDir()
	raw := legacyTaskFixture(t, map[string]any{"inflight_mutation": map[string]any{"action": "select", "class_id": "class", "course_code": "C1"}})
	other := legacyTaskFixture(t, map[string]any{"account": "student-b", "task_id": "other-task"})
	writeLegacyFixture(t, root, "course_selection_tasks.json", "["+string(raw)+","+string(other)+"]")
	preview, err := ImportLegacy(context.Background(), api.database, root, session.Account, "")
	if err != nil || len(preview.Items) != 2 {
		t.Fatal(preview, err)
	}
	applied, err := ImportLegacy(context.Background(), api.database, root, session.Account, preview.Digest)
	if err != nil || applied.Applied != 2 {
		t.Fatal(applied, err)
	}
	repeat, err := ImportLegacy(context.Background(), api.database, root, session.Account, preview.Digest)
	if err != nil || repeat.Applied != 0 {
		t.Fatal(repeat, err)
	}
	task := runSelectionTaskTick(t, api, session, "legacy-task")
	if task.Desired != "paused" || task.Status != "needs_review" || client.writes != 0 || len(task.ImportedOperations) != 1 {
		t.Fatal(task, client.writes)
	}
	body := `{"task_id":"legacy-task"}`
	if response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	task = runSelectionTaskTick(t, api, session, "legacy-task")
	if task.Desired != "paused" || len(task.ImportedOperations) != 1 || client.writes != 0 {
		t.Fatal(task, client.writes)
	}
	client.visible = true
	if response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	task = runSelectionTaskTick(t, api, session, "legacy-task")
	if len(task.ImportedOperations) != 0 || client.writes != 0 {
		t.Fatal(task, client.writes)
	}
	operation := academic.JWXKSelect{Batch: "round", Class: "class", Course: "C1", Scope: "XGKC"}
	attempt, err := api.database.SelectionAttempt(context.Background(), session, jwxkSelectionKey(operation))
	if err != nil || attempt.State != "confirmed" {
		t.Fatal(attempt, err)
	}
}

func TestLegacyTaskVariantsAndConflictingPendingClasses(t *testing.T) {
	for _, status := range []string{"running", "waiting", "draft", "paused", "success", "failed", "cancelled"} {
		task, operations, err := convertLegacyTask(legacyTaskFixture(t, map[string]any{"status": status}))
		if err != nil || len(operations) != 0 || task.Desired == "running" {
			t.Fatal(status, task, err)
		}
		if (status == "success" || status == "failed" || status == "cancelled") && task.Status != status {
			t.Fatal(task)
		}
		payload, _ := json.Marshal(task)
		if strings.Contains(string(payload), "must-not-persist") {
			t.Fatal("school secret copied")
		}
	}
	for _, kind := range []string{"weight_strategy", "vacancy_swap"} {
		extra := map[string]any{"task_type": kind}
		if kind == "weight_strategy" {
			extra["grade_size"] = 100
			extra["weight_status"] = map[string]any{"inflight": map[string]any{"action": "add", "class_id": "class", "course_code": "C1", "weight": 50}}
		} else {
			extra["swap_groups"] = []any{map[string]any{"group_id": "swap", "name": "换课", "target": map[string]any{"class_id": "class", "course_code": "C1", "teaching_class_type": "XGKC"}, "drop_courses": []any{map[string]any{"class_id": "old", "course_code": "C2", "teaching_class_type": "XGKC"}}}}
			extra["swap_results"] = map[string]any{"swap": map[string]any{"pending_drop_ids": []string{"old"}, "target_pending": true}}
		}
		task, operations, err := convertLegacyTask(legacyTaskFixture(t, extra))
		if err != nil || len(operations) == 0 || task.Desired != "paused" {
			t.Fatal(kind, task, err)
		}
		if kind == "weight_strategy" && *operations[0].Selection.Weight != 50 {
			t.Fatal(operations)
		}
		if kind == "vacancy_swap" && (len(operations) != 2 || operations[0].Action != "drop") {
			t.Fatal(operations)
		}
	}
	raw := legacyTaskFixture(t, map[string]any{"inflight_mutation": map[string]any{"action": "select", "class_id": "different", "course_code": "C1", "teaching_class_type": "XGKC"}, "group_results": map[string]any{"g": map[string]any{"pending_class_ids": []string{"class"}}}})
	if _, _, err := convertLegacyTask(raw); err == nil {
		t.Fatal("conflicting course operations silently discarded")
	}
}

func TestLegacyAutomationSettingsAccountOwnershipAndValidation(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	loginCookie(t, handler, "student-a")
	root := t.TempDir()
	writeLegacyFixture(t, root, "course_selection_automation_settings.json", `{"student-a:round":{"strategy_schedule_mode":"final_windows","final_check_minutes":10,"mail_enabled":true,"batch_name":"旧轮次"},"student-b:round":{"rebalance_seconds":600}}`)
	preview, err := ImportLegacy(context.Background(), api.database, root, "student-a", "")
	if err != nil || len(preview.Items) != 1 {
		t.Fatal(preview, err)
	}
	if _, err := ImportLegacy(context.Background(), api.database, root, "student-a", preview.Digest); err != nil {
		t.Fatal(err)
	}
	document, err := api.database.Read(context.Background(), "student-a", jwxkAutomationSettingsNamespace, "round")
	var settings academic.JWXKAutomationSettings
	if err != nil || json.Unmarshal(document.Payload, &settings) != nil || settings.StrategyScheduleMode != "final_windows" || settings.FinalCheckMinutes != 10 || settings.RebalanceSeconds != 1800 || !settings.MailEnabled {
		t.Fatal(settings, err)
	}
	writeLegacyFixture(t, root, "course_selection_automation_settings.json", `{"student-a:round":{"rebalance_seconds":1}}`)
	if _, err := ImportLegacy(context.Background(), api.database, root, "student-a", ""); err == nil {
		t.Fatal("invalid imported timing accepted")
	}
}

func TestImportedDropVerificationNeverRepeatsRemoval(t *testing.T) {
	for _, present := range []bool{true, false} {
		client := &swapTaskClient{drops: map[string]bool{"old": present}}
		api, _, _, session, id := swapTaskSetup(t, client)
		task := readSelectionTask(t, api, session, id)
		operation := academic.JWXKImportedOperation{Action: "drop", Course: "C-old", Removal: &academic.JWXKDeselect{Batch: task.Batch, Class: "old"}}
		task.ImportedOperations = []academic.JWXKImportedOperation{operation}
		key, _ := importedOperationKey(operation)
		attempt, err := api.database.BeginSelectionOperation(context.Background(), session, key, operation.Course, task.Term, task.Batch, "old")
		if err != nil {
			t.Fatal(err)
		}
		if err := api.database.FinishSelection(context.Background(), session, key, attempt, "unknown"); err != nil {
			t.Fatal(err)
		}
		if err := api.verifyImportedTaskOperation(context.Background(), api.accounts.Runtime(session), session, &task, "direct"); err != nil {
			t.Fatal(err)
		}
		if client.removals != 0 || client.writes != 0 {
			t.Fatal("verification mutated school", client.removals, client.writes)
		}
		if present && (len(task.ImportedOperations) != 1 || task.Status != "needs_review") || !present && len(task.ImportedOperations) != 0 {
			t.Fatal(task)
		}
	}
}
