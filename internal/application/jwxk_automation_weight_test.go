package application

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type weightTaskClient struct {
	weightApplyClient
	ended         bool
	missingMarket bool
}

func (client *weightTaskClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if client.missingMarket && input.Path == "/xsxk/elective/clazz/list" {
		return []byte(`{"code":200,"data":{"total":2,"rows":[{"KCH":"A","JXBID":"A","KRL":10},{"KCH":"B","JXBID":"B","KRL":10}]}}`), nil
	}
	if client.ended && input.Path == "/xsxk/web/now" {
		return []byte(`{"code":200,"data":{"currentTime":1924992000000}}`), nil
	}
	return client.weightApplyClient.RequestService(ctx, input)
}

func weightTaskSetup(t *testing.T, client *weightTaskClient) (*API, http.Handler, *http.Cookie, store.Session, string) {
	t.Helper()
	api, handler := testApplication(t, "remote-multi")
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	api.database.AddAccount(context.Background(), "student-a")
	client.account = "student-a"
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	body := `{"batch_code":"round","term_code":"2026-2027-1","task_type":"weight_strategy","grade_size":5000,"groups":[{"group_id":"g","name":"目标","target_count":1}],"items":[{"class_id":"A","course_code":"A","teaching_class_type":"FANKC","plan_group_id":"g","utility":9},{"class_id":"B","course_code":"B","teaching_class_type":"FANKC","plan_group_id":"g","utility":1}]}`
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks", body, cookie)
	if response.Code != 200 {
		t.Fatal(response)
	}
	var task academic.JWXKAutomationTask
	json.Unmarshal(response.Body.Bytes(), &task)
	generation, _ := api.database.Generation(context.Background(), "student-a")
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+task.ID+`"}`, cookie)
	if response.Code != 200 {
		t.Fatal(response)
	}
	return api, handler, cookie, store.Session{Account: "student-a", Generation: generation}, task.ID
}

func prepareWeightTaskMarket(t *testing.T, api *API, session store.Session, id string) academic.JWXKAutomationTask {
	t.Helper()
	task := runSelectionTaskTick(t, api, session, id)
	if task.WeightScanJob == "" {
		t.Fatal("market scan was not queued", task)
	}
	job, exists := api.resources.Job(session, task.WeightScanJob)
	if !exists {
		t.Fatal("shared market job missing")
	}
	select {
	case <-job.done:
	case <-time.After(5 * time.Second):
		t.Fatal("market scan blocked on account slot")
	}
	task = runSelectionTaskTick(t, api, session, id)
	if task.WeightApplication == nil || task.WeightScanJob != "" {
		t.Fatal("complete market did not produce a durable plan", task)
	}
	return task
}

func TestWeightTaskRefreshesMarketThenAppliesOneStepAtATime(t *testing.T) {
	client := &weightTaskClient{weightApplyClient: weightApplyClient{weights: map[string]int{"A": 5, "B": 25}}}
	api, _, _, session, id := weightTaskSetup(t, client)
	prepareWeightTaskMarket(t, api, session, id)
	if len(client.writes) != 0 {
		t.Fatal("model generation wrote to school")
	}
	completed := false
	for step := 0; step < 8; step++ {
		before := len(client.writes)
		task := runSelectionTaskTick(t, api, session, id)
		if len(client.writes)-before > 1 || task.Status == "needs_review" {
			t.Fatal("step limit or valid strategy failed", task, client.writes)
		}
		if task.WeightApplication == nil && task.WeightStatus["phase"] == "idle" {
			completed = true
			break
		}
	}
	if !completed || len(client.writes) == 0 {
		t.Fatal("strategy did not complete", client.writes)
	}
	before := len(client.writes)
	task := runSelectionTaskTick(t, api, session, id)
	if task.WeightScanJob != "" || len(client.writes) != before {
		t.Fatal("strategy bypassed minimum interval", task)
	}
	client.weights = map[string]int{}
	task.WeightStatus["retry_strategy"] = true
	if err := api.saveAutomationTask(context.Background(), session, &task); err != nil {
		t.Fatal(err)
	}
	prepareWeightTaskMarket(t, api, session, id)
	for step := 0; step < 8; step++ {
		task = runSelectionTaskTick(t, api, session, id)
		if task.WeightApplication == nil || task.Status == "needs_review" {
			break
		}
	}
	if task.WeightApplication != nil || task.Status == "needs_review" || len(client.writes) <= before {
		t.Fatal("fresh cycle did not handle externally removed old result", task, client.writes)
	}
}

func TestWeightTaskUnknownWithdrawalPausesAndEndedRoundNeverStartsNext(t *testing.T) {
	client := &weightTaskClient{weightApplyClient: weightApplyClient{weights: map[string]int{"A": 5, "B": 25}, unknownDrop: true}}
	api, handler, cookie, session, id := weightTaskSetup(t, client)
	prepareWeightTaskMarket(t, api, session, id)
	task := runSelectionTaskTick(t, api, session, id)
	if task.Status != "needs_review" || task.WeightApplication == nil || len(client.writes) != 1 {
		t.Fatal(task, client.writes)
	}
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	fresh := NewAPI(api.database, api.accounts, "test")
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	task = runSelectionTaskTick(t, fresh, session, id)
	if task.Status != "needs_review" || len(client.writes) != 1 {
		t.Fatal("unknown write replayed after restart", task, client.writes)
	}
	delete(client.weights, "A")
	client.ended = true
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	runSelectionTaskTick(t, fresh, session, id)
	task = runSelectionTaskTick(t, fresh, session, id)
	if task.Status != "failed" || len(client.writes) != 1 {
		t.Fatal("ended round continued new writes", task, client.writes)
	}
}

func TestWeightStrategyTriggerHonorsIntervalAndFinalWindows(t *testing.T) {
	now := time.Date(2026, 11, 15, 15, 0, 0, 0, time.UTC)
	settings := academic.DefaultJWXKAutomationSettings()
	task := academic.JWXKAutomationTask{WeightStatus: map[string]any{"last_strategy_at": now.Format(time.RFC3339Nano)}}
	end := now.Add(time.Hour)
	if trigger := weightStrategyTrigger(&task, settings, now.Add(time.Minute), end); trigger != "" {
		t.Fatal(trigger)
	}
	if trigger := weightStrategyTrigger(&task, settings, now.Add(30*time.Minute), end); trigger != "interval" {
		t.Fatal(trigger)
	}
	settings.StrategyScheduleMode = "final_windows"
	if trigger := weightStrategyTrigger(&task, settings, end.Add(-3*time.Minute), end); trigger != "final_check" {
		t.Fatal(trigger)
	}
	task.WeightStatus["final_check_executed"] = true
	task.WeightStatus["final_notice_executed"] = true
	if trigger := weightStrategyTrigger(&task, settings, end.Add(-time.Minute), end); trigger != "" {
		t.Fatal("repeated final window", trigger)
	}
}

func TestWeightTaskEndedBeforeFirstWriteRetainsUnsentPlanWithoutSubmitting(t *testing.T) {
	client := &weightTaskClient{weightApplyClient: weightApplyClient{weights: map[string]int{"A": 5, "B": 25}}}
	api, _, _, session, id := weightTaskSetup(t, client)
	prepareWeightTaskMarket(t, api, session, id)
	client.ended = true
	task := runSelectionTaskTick(t, api, session, id)
	if task.Status != "failed" || len(client.writes) != 0 {
		t.Fatal(task, client.writes)
	}
}

func TestWeightTaskMissingMarketDoesNotCalculateOrWrite(t *testing.T) {
	client := &weightTaskClient{weightApplyClient: weightApplyClient{weights: map[string]int{"A": 5, "B": 25}}, missingMarket: true}
	api, _, _, session, id := weightTaskSetup(t, client)
	task := runSelectionTaskTick(t, api, session, id)
	job, exists := api.resources.Job(session, task.WeightScanJob)
	if !exists {
		t.Fatal(task)
	}
	select {
	case <-job.done:
	case <-time.After(5 * time.Second):
		t.Fatal("scan did not finish")
	}
	task = runSelectionTaskTick(t, api, session, id)
	if task.WeightApplication != nil || len(client.writes) != 0 || academic.Text(task.Execution["last_error_type"]) == "" {
		t.Fatal("missing bidders produced executable strategy", task)
	}
}
