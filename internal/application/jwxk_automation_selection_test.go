package application

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type selectionTaskClient struct {
	jwxkMutationClient
	ended, fullFirst, missingFirst bool
	selectedClass                  string
}

func (client *selectionTaskClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if input.Path == "/xsxk/web/now" && client.ended {
		return []byte(`{"code":200,"data":{"currentTime":1924992000000}}`), nil
	}
	if input.Path == "/xsxk/elective/clazz/list" {
		var query map[string]any
		json.Unmarshal(input.JSON, &query)
		if client.missingFirst && query["KEY"] == "C1" {
			return []byte(`{"code":200,"data":{"total":0,"rows":[]}}`), nil
		}
		selected := 0
		if client.fullFirst {
			selected = 10
		}
		return json.Marshal(map[string]any{"code": 200, "data": map[string]any{"total": 2, "rows": []map[string]any{{"KCH": "C1", "JXBID": "class", "KRL": 10, "YXRS": selected, "secretVal": "private"}, {"KCH": "C2", "JXBID": "other", "KRL": 10, "YXRS": 0, "secretVal": "private"}}}})
	}
	if input.Path == "/xsxk/elective/clazz/add" {
		client.selectedClass = input.Form.Get("clazzId")
	}
	if input.Path == "/xsxk/elective/select" && client.visible && client.selectedClass == "other" {
		return []byte(`{"code":200,"data":[{"KCH":"C2","JXBID":"other","KRL":10,"YXRS":1}]}`), nil
	}
	return client.jwxkMutationClient.RequestService(ctx, input)
}

func selectionTaskSetup(t *testing.T, client *selectionTaskClient) (*API, http.Handler, *http.Cookie, store.Session, string) {
	t.Helper()
	return selectionTaskSetupAt(t, client, t.TempDir())
}

func selectionTaskSetupAt(t *testing.T, client *selectionTaskClient, directory string) (*API, http.Handler, *http.Cookie, store.Session, string) {
	t.Helper()
	api, handler := testApplicationAt(t, "remote-multi", directory)
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	api.database.AddAccount(context.Background(), "student-a")
	client.account = "student-a"
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks", strings.ReplaceAll(automationTaskBody, "FANKC", "XGKC"), cookie)
	if response.Code != 200 {
		t.Fatal(response)
	}
	var task academic.JWXKAutomationTask
	json.Unmarshal(response.Body.Bytes(), &task)
	generation, _ := api.database.Generation(context.Background(), "student-a")
	return api, handler, cookie, store.Session{Account: "student-a", Generation: generation}, task.ID
}

func readSelectionTask(t *testing.T, api *API, session store.Session, id string) academic.JWXKAutomationTask {
	t.Helper()
	document, err := api.database.Read(context.Background(), session.Account, jwxkAutomationTaskNamespace, id)
	if err != nil {
		t.Fatal(err)
	}
	var task academic.JWXKAutomationTask
	if err := json.Unmarshal(document.Payload, &task); err != nil {
		t.Fatal(err)
	}
	task.Revision = document.Revision
	return task
}

func runSelectionTaskTick(t *testing.T, api *API, session store.Session, id string) academic.JWXKAutomationTask {
	t.Helper()
	task := readSelectionTask(t, api, session, id)
	task.NextAttempt = ""
	if err := api.saveAutomationTask(context.Background(), session, &task); err != nil {
		t.Fatal(err)
	}
	err := api.resources.documentWorkStep(context.Background(), session, func() error {
		return api.tickJWXKSelectionTask(context.Background(), api.accounts.Runtime(session), session, id)
	})
	if err != nil {
		t.Log("tick retained a retryable error:", err)
	}
	return readSelectionTask(t, api, session, id)
}

func TestSelectionTaskRunsOnlyAfterStartAndStopsAtConfirmedGoal(t *testing.T) {
	client := &selectionTaskClient{}
	api, handler, cookie, session, id := selectionTaskSetup(t, client)
	if task := runSelectionTaskTick(t, api, session, id); task.Status != "draft" || client.writes != 0 {
		t.Fatal(task, client.writes)
	}
	body := `{"task_id":"` + id + `"}`
	if response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/pause", body, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	if task := runSelectionTaskTick(t, api, session, id); task.Status != "paused" || client.writes != 0 {
		t.Fatal(task)
	}
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie)
	task := runSelectionTaskTick(t, api, session, id)
	if client.writes != 1 || task.PendingSelection != nil {
		t.Fatal(task, client.writes)
	}
	task = runSelectionTaskTick(t, api, session, id)
	if task.Status != "success" || task.Desired != "paused" || client.writes != 1 {
		t.Fatal(task, client.writes)
	}
	response := apiRequest(handler, "GET", "/api/course-selection/jwxk/automation/tasks", "", cookie)
	if !strings.Contains(response.Body.String(), `"success_count":1`) || !strings.Contains(response.Body.String(), `"poll_interval_seconds":15`) {
		t.Fatal("task UI contract is incomplete", response)
	}
	if response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie); response.Code != 409 {
		t.Fatal("completed task restarted", response)
	}
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/cancel", body, cookie)
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/automation/tasks", "", cookie)
	if !strings.Contains(response.Body.String(), `"tasks":[]`) {
		t.Fatal(response)
	}
}

func TestSelectionTaskUnknownSubmissionSurvivesRestartAndRoundEnd(t *testing.T) {
	client := &selectionTaskClient{jwxkMutationClient: jwxkMutationClient{uncertain: true}}
	directory := t.TempDir()
	phase := os.Getenv("TOOLBOX_TEST_REBOOT_PHASE")
	if phase != "" {
		directory = os.Getenv("TOOLBOX_TEST_REBOOT_DIR")
		if !filepath.IsAbs(directory) || (phase != "prepare" && phase != "recover") {
			t.Fatal("invalid guest reboot fixture")
		}
	}
	var id string
	if phase != "recover" {
	prepared := t.Run("before restart", func(t *testing.T) {
		api, handler, cookie, session, taskID := selectionTaskSetupAt(t, client, directory)
		id = taskID
		apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
		task := runSelectionTaskTick(t, api, session, id)
		if task.PendingSelection == nil || client.writes != 1 {
			t.Fatal(task, client.writes)
		}
	})
	if !prepared { t.FailNow() }
	if phase == "prepare" {
		if err := os.WriteFile(filepath.Join(directory, "fixture-task-id"), []byte(id), 0600); err != nil { t.Fatal(err) }
		return
	}
	} else {
		stored, err := os.ReadFile(filepath.Join(directory, "fixture-task-id"))
		if err != nil { t.Fatal(err) }
		id = string(stored)
	}
	writesBeforeRecovery := client.writes
	client.account = "student-a"
	fresh, handler := testApplicationAt(t, "remote-multi", directory)
	if phase == "recover" {
		client.visible = true
	} else {
		fresh.accounts.mutex.Lock()
		fresh.accounts.automationTick = nil
		fresh.accounts.mutex.Unlock()
	}
	fresh.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	generation, err := fresh.database.Generation(context.Background(), "student-a")
	if err != nil {
		t.Fatal(err)
	}
	session := store.Session{Account: "student-a", Generation: generation}
	if phase == "recover" {
		deadline := time.Now().Add(90 * time.Second)
		for time.Now().Before(deadline) {
			task := readSelectionTask(t, fresh, session, id)
			if task.Status == "success" {
				if task.PendingSelection != nil || client.writes != 0 {
					t.Fatal("recovery did not reconcile read-only", task, client.writes)
				}
				return
			}
			time.Sleep(200 * time.Millisecond)
		}
		t.Fatal("background scheduler did not reconcile the persisted selection")
	}
	body := `{"task_id":"` + id + `"}`
	task := runSelectionTaskTick(t, fresh, session, id)
	if task.PendingSelection == nil || client.writes != writesBeforeRecovery {
		t.Fatal("restart replayed pending write", task, client.writes)
	}
	client.ended = true
	task = runSelectionTaskTick(t, fresh, session, id)
	if task.Status != "needs_review" || task.Desired != "paused" || client.writes != writesBeforeRecovery {
		t.Fatal(task, client.writes)
	}
	client.visible = true
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie)
	task = runSelectionTaskTick(t, fresh, session, id)
	if task.Status != "success" || task.PendingSelection != nil || client.writes != writesBeforeRecovery {
		t.Fatal("ended round did not reconcile read-only", task, client.writes)
	}
}

func TestSelectionTaskOnlyDowngradesExplicitFullCandidate(t *testing.T) {
	for _, missing := range []bool{false, true} {
		client := &selectionTaskClient{fullFirst: !missing, missingFirst: missing}
		api, handler, cookie, session, id := selectionTaskSetup(t, client)
		task := readSelectionTask(t, api, session, id)
		task.Items = append(task.Items, map[string]any{"class_id": "other", "course_code": "C2", "teaching_class_type": "XGKC", "plan_group_id": "g", "utility": 1})
		api.saveAutomationTask(context.Background(), session, &task)
		apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
		task = runSelectionTaskTick(t, api, session, id)
		if client.writes != 0 {
			t.Fatal("wrote before live candidate check")
		}
		task = runSelectionTaskTick(t, api, session, id)
		if missing {
			if client.writes != 0 || task.RejectedClasses["class"] != "" {
				t.Fatal("missing candidate was downgraded", task)
			}
		} else if client.writes != 1 || client.selectedClass != "other" {
			t.Fatal("full candidate did not advance", task, client.writes)
		}
	}
}

func TestSelectionTaskSchedulerDeduplicatesAndHonorsNextAttempt(t *testing.T) {
	client := &selectionTaskClient{}
	api, handler, cookie, session, id := selectionTaskSetup(t, client)
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	release, err := api.resources.acquireAccount(context.Background(), session)
	if err != nil {
		t.Fatal(err)
	}
	api.scheduleAutomationTasks()
	api.scheduleAutomationTasks()
	api.resources.mutex.Lock()
	jobs := []*CacheJob{}
	for _, job := range api.resources.jobs {
		if job.Resource == "jwxk-automation" && job.Variant == id {
			jobs = append(jobs, job)
		}
	}
	api.resources.mutex.Unlock()
	release()
	if len(jobs) != 1 {
		t.Fatalf("scheduled %d jobs for one task", len(jobs))
	}
	select {
	case <-jobs[0].done:
	case <-time.After(5 * time.Second):
		t.Fatal("scheduler did not finish")
	}
	if task := readSelectionTask(t, api, session, id); task.Attempts != 1 || client.writes != 1 {
		t.Fatal(task, client.writes)
	}
	api.scheduleAutomationTasks()
	api.resources.mutex.Lock()
	defer api.resources.mutex.Unlock()
	count := 0
	for _, job := range api.resources.jobs {
		if job.Resource == "jwxk-automation" && job.Variant == id {
			count++
		}
	}
	if count != 1 {
		t.Fatal("scheduler bypassed next-attempt deadline", count)
	}
}

func TestSelectionTaskCheckNowPreservesBackoffAndCancelStopsWrites(t *testing.T) {
	client := &selectionTaskClient{missingFirst: true}
	api, handler, cookie, session, id := selectionTaskSetup(t, client)
	body := `{"task_id":"` + id + `"}`
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", body, cookie)
	task := runSelectionTaskTick(t, api, session, id)
	deadline := task.NextAttempt
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/check_now", body, cookie)
	if response.Code != 200 || readSelectionTask(t, api, session, id).NextAttempt != deadline {
		t.Fatal("manual check bypassed error backoff", response)
	}
	if err := api.tickJWXKSelectionTask(context.Background(), api.accounts.Runtime(session), session, id); err != nil {
		t.Fatal(err)
	}
	if readSelectionTask(t, api, session, id).Attempts != task.Attempts || client.writes != 0 {
		t.Fatal("not-yet-due task executed")
	}
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/cancel", body, cookie)
	client.missingFirst = false
	if task := runSelectionTaskTick(t, api, session, id); task.Status != "cancelled" || client.writes != 0 {
		t.Fatal("cancelled task wrote", task)
	}
}

func TestSelectionTaskUsesOfficialClockForConfiguredWindow(t *testing.T) {
	for _, ended := range []bool{false, true} {
		client := &selectionTaskClient{}
		api, handler, cookie, session, id := selectionTaskSetup(t, client)
		task := readSelectionTask(t, api, session, id)
		if ended {
			task.End = "2021-01-01T00:00:00+08:00"
		} else {
			task.Start = "2029-01-01T00:00:00+08:00"
		}
		if err := api.saveAutomationTask(context.Background(), session, &task); err != nil {
			t.Fatal(err)
		}
		apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
		task = runSelectionTaskTick(t, api, session, id)
		if client.writes != 0 || ended && task.Status != "failed" || !ended && task.Status != "waiting" {
			t.Fatal("configured window ignored", task, client.writes)
		}
		if !ended {
			deadline, _ := time.Parse(time.RFC3339Nano, task.NextAttempt)
			if time.Until(deadline) < 10*time.Second {
				t.Fatal("opening burst polled too early", task)
			}
		}
	}
}

func TestSelectionTaskRevokedAccountCannotPersistOrSubmit(t *testing.T) {
	client := &selectionTaskClient{}
	api, handler, cookie, session, id := selectionTaskSetup(t, client)
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	runtime := api.accounts.Runtime(session)
	if err := api.accounts.Stop(context.Background(), session.Account, false); err != nil {
		t.Fatal(err)
	}
	if err := api.tickJWXKSelectionTask(context.Background(), runtime, session, id); err == nil {
		t.Fatal("revoked identity was allowed to execute")
	}
	if client.writes != 0 {
		t.Fatal("revoked account submitted a write")
	}
}
