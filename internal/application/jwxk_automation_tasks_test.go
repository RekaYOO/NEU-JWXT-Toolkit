package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

const automationTaskBody = `{"batch_code":"round","term_code":"term","groups":[{"group_id":"g","name":"目标","target_count":1}],"items":[{"class_id":"class","course_code":"C1","teaching_class_type":"FANKC","plan_group_id":"g","secretVal":"must-not-persist"}]}`

func TestAutomationDraftCreationReplacementIsolationAndPlanSync(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(ctx, account)
	}
	clients := map[string]*jwxkSelectedClient{}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: account}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	clients["student-a"].Close()
	endpoint := "/api/course-selection/jwxk/automation/tasks"
	response := apiRequest(handler, "POST", endpoint, automationTaskBody, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"draft"`) || strings.Contains(response.Body.String(), "must-not-persist") {
		t.Fatal(response)
	}
	var first academic.JWXKAutomationTask
	json.Unmarshal(response.Body.Bytes(), &first)
	response = apiRequest(handler, "POST", endpoint, automationTaskBody, cookieA)
	if response.Code != 409 {
		t.Fatal("duplicate task created", response)
	}
	response = apiRequest(handler, "POST", endpoint, strings.Replace(automationTaskBody, `"batch_code"`, `"replace_existing":true,"batch_code"`, 1), cookieA)
	if response.Code != 200 {
		t.Fatal(response)
	}
	var second academic.JWXKAutomationTask
	json.Unmarshal(response.Body.Bytes(), &second)
	if second.ID == first.ID {
		t.Fatal("replacement reused identity")
	}
	previous, err := api.database.Read(ctx, "student-a", jwxkAutomationTaskNamespace, first.ID)
	if err != nil || !strings.Contains(string(previous.Payload), `"status":"cancelled"`) {
		t.Fatal(previous, err)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 200 || strings.Contains(response.Body.String(), first.ID) || !strings.Contains(response.Body.String(), second.ID) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"tasks":[]`) {
		t.Fatal(response)
	}
	plan := strings.Replace(automationTaskBody, `"target_count":1`, `"target_count":2`, 1)
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/plan/save", plan, cookieA)
	if response.Code != 200 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if !strings.Contains(response.Body.String(), `"target_count":2`) {
		t.Fatal("bound task not updated", response)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/sync-batch-times", `{"batch_code":"round","start_at":"2026-11-20T08:00:00+08:00","end_at":"2026-11-20T10:00:00+08:00"}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"changed_task_count":1`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if !strings.Contains(response.Body.String(), `"start_at":"2026-11-20T08:00:00+08:00"`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/plan/save", `{"batch_code":"round","term_code":"term","revision":1,"items":[],"groups":[]}`, cookieA)
	if response.Code != 200 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if !strings.Contains(response.Body.String(), `"status":"paused"`) {
		t.Fatal("empty task not paused", response)
	}
	if clients["student-a"].calls != 0 || clients["student-b"].calls != 0 {
		t.Fatal("draft creation contacted school")
	}
}
