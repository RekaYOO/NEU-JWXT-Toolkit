package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type swapTaskClient struct {
	selectionTaskClient
	drops           map[string]bool
	removals        int
	uncertainDrop   bool
	missingCapacity bool
}

func (client *swapTaskClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if input.Path == "/xsxk/elective/select" {
		rows := []map[string]any{}
		for class, present := range client.drops {
			if present {
				rows = append(rows, map[string]any{"KCH": "C-" + class, "JXBID": class, "KRL": 10, "YXRS": 1, "secretVal": "private", "teachingClassType": "XGKC"})
			}
		}
		if client.visible {
			rows = append(rows, map[string]any{"KCH": "C1", "JXBID": "class", "KRL": 10, "YXRS": 1, "secretVal": "private", "teachingClassType": "XGKC"})
		}
		return json.Marshal(map[string]any{"code": 200, "data": rows})
	}
	if input.Path == "/xsxk/elective/neu/clazz/del" {
		client.removals++
		if client.uncertainDrop {
			return nil, errors.New("response lost")
		}
		client.drops[input.Form.Get("clazzId")] = false
		return []byte(`{"code":200}`), nil
	}
	if input.Path == "/xsxk/elective/clazz/list" && client.missingCapacity {
		return []byte(`{"code":200,"data":{"total":1,"rows":[{"KCH":"C1","JXBID":"class","secretVal":"private"}]}}`), nil
	}
	return client.selectionTaskClient.RequestService(ctx, input)
}

func swapTaskSetup(t *testing.T, client *swapTaskClient) (*API, http.Handler, *http.Cookie, store.Session, string) {
	t.Helper()
	api, handler := testApplication(t, "remote-multi")
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	api.database.AddAccount(context.Background(), "student-a")
	client.account = "student-a"
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	body := `{"batch_code":"round","term_code":"term","task_type":"vacancy_swap","groups":[],"items":[],"swap_groups":[{"group_id":"swap","name":"换课","target":{"class_id":"class","course_code":"C1","teaching_class_type":"XGKC"},"drop_courses":[{"class_id":"drop1","course_code":"C-drop1","teaching_class_type":"XGKC"},{"class_id":"drop2","course_code":"C-drop2","teaching_class_type":"XGKC"}]}]}`
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

func TestSwapTaskConfirmsEachDropBeforeNextOperation(t *testing.T) {
	client := &swapTaskClient{drops: map[string]bool{"drop1": true, "drop2": true}}
	api, _, _, session, id := swapTaskSetup(t, client)
	for expected := 1; expected <= 2; expected++ {
		task := runSelectionTaskTick(t, api, session, id)
		if task.PendingDrop != nil || client.removals != expected || client.writes != 0 {
			t.Fatal("drop not individually confirmed", task, client.removals, client.writes)
		}
	}
	task := runSelectionTaskTick(t, api, session, id)
	if task.PendingSelection != nil || client.writes != 1 {
		t.Fatal("target did not follow confirmed drops", task, client.writes)
	}
	task = runSelectionTaskTick(t, api, session, id)
	if task.Status != "success" || client.removals != 2 || client.writes != 1 {
		t.Fatal(task)
	}
}

func TestSwapTaskUnknownDropStopsAndSurvivesRestart(t *testing.T) {
	client := &swapTaskClient{drops: map[string]bool{"drop1": true, "drop2": true}, uncertainDrop: true}
	api, handler, cookie, session, id := swapTaskSetup(t, client)
	task := runSelectionTaskTick(t, api, session, id)
	if task.Status != "needs_review" || task.PendingDrop == nil || client.removals != 1 || client.writes != 0 {
		t.Fatal(task)
	}
	fresh := NewAPI(api.database, api.accounts, "test")
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	task = runSelectionTaskTick(t, fresh, session, id)
	if client.removals != 1 || client.writes != 0 || task.PendingDrop == nil {
		t.Fatal("restart replayed drop", task)
	}
	client.drops["drop1"] = false
	client.ended = true
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	task = runSelectionTaskTick(t, fresh, session, id)
	if task.PendingDrop != nil || client.removals != 1 || client.writes != 0 {
		t.Fatal("ended round did not reconcile without new drop", task)
	}
	task = runSelectionTaskTick(t, fresh, session, id)
	if task.Status != "failed" || client.removals != 1 || client.writes != 0 {
		t.Fatal(task)
	}
}

func TestSwapTaskMissingOrFullMarketPreservesExistingCourses(t *testing.T) {
	for _, missing := range []bool{true, false} {
		client := &swapTaskClient{drops: map[string]bool{"drop1": true, "drop2": true}, missingCapacity: missing}
		client.fullFirst = !missing
		api, _, _, session, id := swapTaskSetup(t, client)
		runSelectionTaskTick(t, api, session, id)
		if client.removals != 0 || client.writes != 0 {
			t.Fatal("uncertain/full vacancy caused mutation")
		}
	}
}

func TestSwapTaskUnknownTargetOnlyVerifiesOnResume(t *testing.T) {
	client := &swapTaskClient{drops: map[string]bool{}}
	client.uncertain = true
	api, handler, cookie, session, id := swapTaskSetup(t, client)
	task := runSelectionTaskTick(t, api, session, id)
	if task.Status != "needs_review" || task.PendingSelection == nil || client.writes != 1 {
		t.Fatal(task)
	}
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	task = runSelectionTaskTick(t, api, session, id)
	if client.writes != 1 || task.PendingSelection == nil {
		t.Fatal("unknown target was submitted again", task)
	}
	client.visible = true
	apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie)
	runSelectionTaskTick(t, api, session, id)
	task = runSelectionTaskTick(t, api, session, id)
	if task.Status != "success" || task.PendingSelection != nil || client.writes != 1 {
		t.Fatal("late confirmation did not finish swap", task)
	}
}

func TestSwapTaskUnsentDropIntentRechecksVacancy(t *testing.T) {
	client := &swapTaskClient{drops: map[string]bool{"drop1": true, "drop2": true}}
	client.fullFirst = true
	api, _, _, session, id := swapTaskSetup(t, client)
	task := readSelectionTask(t, api, session, id)
	task.PendingDrop = &academic.JWXKDeselect{Batch: "round", Class: "drop1", Source: "yxkcyx"}
	task.PendingDropGroup = "swap"
	if err := api.saveAutomationTask(context.Background(), session, &task); err != nil {
		t.Fatal(err)
	}
	task = runSelectionTaskTick(t, api, session, id)
	if task.PendingDrop != nil || client.removals != 0 || client.writes != 0 {
		t.Fatal("unsent intent bypassed fresh vacancy check", task)
	}
}
