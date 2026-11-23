package academic

import (
	"encoding/json"
	"testing"
	"time"
)

func automationTaskFixture(t *testing.T) JWXKAutomationTask {
	t.Helper()
	request := DefaultJWXKAutomationRequest()
	err := json.Unmarshal([]byte(`{"batch_code":"round","term_code":"term","groups":[{"group_id":"g","name":"目标","target_count":1}],"items":[{"class_id":"class","course_code":"C1","teaching_class_type":"FANKC","plan_group_id":"g","secretVal":"must-not-persist","priority":2}],"start_at":"2026-11-17T08:00:00+08:00","end_at":"2026-11-17T10:00:00+08:00","poll_seconds":1}`), &request)
	if err != nil {
		t.Fatal(err)
	}
	task, err := NewJWXKAutomationTask(request, "task", time.Date(2026, 11, 16, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	return task
}

func TestAutomationLifecyclePreservesDraftAndReviewSemantics(t *testing.T) {
	task := automationTaskFixture(t)
	if task.Status != "draft" || task.Desired != "paused" || task.Items[0]["utility"] != 9 || task.Items[0]["secretVal"] != nil {
		t.Fatal(task)
	}
	before := time.Date(2026, 11, 17, 7, 0, 0, 0, time.FixedZone("school", 28800))
	if task.Action("check_now", before) == nil {
		t.Fatal("draft scheduled immediate request")
	}
	if err := task.Action("start", before); err != nil || task.Status != "waiting" || !task.RestartReconcile || task.PollInterval() != time.Second {
		t.Fatal(task, err)
	}
	after := before.Add(2 * time.Hour)
	if err := task.Action("pause", after); err != nil {
		t.Fatal(err)
	}
	if err := task.Action("start", after); err != nil || task.PollInterval() != 15*time.Second {
		t.Fatal(task, err)
	}
	task.Status = "needs_review"
	if err := task.Action("start", after); err != nil || !task.RestartReconcile {
		t.Fatal(err)
	}
	task.Status = "success"
	if task.Action("start", after) == nil {
		t.Fatal("terminal task restarted")
	}
	if err := task.Action("cancel", after); err != nil || task.Desired != "cancelled" {
		t.Fatal(err)
	}
}

func TestAutomationPlanSyncPreservesPendingEvidenceAndPausesEmptyGroups(t *testing.T) {
	task := automationTaskFixture(t)
	task.Type = "weight_strategy"
	task.WeightStatus = map[string]any{"inflight": map[string]any{"class_id": "old"}, "recommendation": []any{"stale"}}
	plan := task.JWXKSavedPlan
	plan.Groups = []JWXKPlanGroup{{"g", "新目标", 2}}
	if !task.SyncPlan(plan, time.Now()) || task.Groups[0].Target != 2 || Object(task.WeightStatus["inflight"])["class_id"] != "old" {
		t.Fatal(task)
	}
	plan.Groups, plan.Items = []JWXKPlanGroup{}, []map[string]any{}
	if !task.SyncPlan(plan, time.Now()) || task.Status != "paused" || len(task.BoundGroups) != 1 {
		t.Fatal(task)
	}
}

func TestAutomationRejectsCatalogScopesAndInvalidGroups(t *testing.T) {
	for _, scenario := range []string{"scope", "group", "time", "grade"} {
		task := automationTaskFixture(t)
		switch scenario {
		case "scope":
			task.Items[0]["teaching_class_type"] = "ALLKC"
		case "group":
			task.Items[0]["plan_group_id"] = "missing"
		case "time":
			task.End = task.Start
		case "grade":
			task.Type = "weight_strategy"
		}
		if task.JWXKAutomationRequest.Valid() {
			t.Fatal("invalid task accepted", scenario)
		}
	}
}

func TestAutomationTimeSyncRetainsUncertainOperations(t *testing.T) {
	task := automationTaskFixture(t)
	task.Type = "weight_strategy"
	task.WeightStatus = map[string]any{"inflight": map[string]any{"class_id": "class"}, "final_check_executed": true}
	task.Desired, task.Status = "running", "running"
	now := time.Date(2026, 11, 18, 0, 0, 0, 0, time.UTC)
	changed, err := task.SyncTimes("2026-11-19T08:00:00+08:00", "2026-11-19T10:00:00+08:00", now)
	if err != nil || !changed || task.Status != "waiting" || task.WeightStatus["final_check_executed"] != nil || Object(task.WeightStatus["inflight"])["class_id"] != "class" {
		t.Fatal(task, err)
	}
	changed, err = task.SyncTimes("2026-11-16T08:00:00+08:00", "2026-11-16T10:00:00+08:00", now)
	if err != nil || !changed || task.Status != "paused" || task.Desired != "paused" {
		t.Fatal(task, err)
	}
	task.Status = "success"
	changed, err = task.SyncTimes("2026-11-20T08:00:00+08:00", "2026-11-20T10:00:00+08:00", now)
	if err != nil || changed {
		t.Fatal("completed task time changed", err)
	}
}
