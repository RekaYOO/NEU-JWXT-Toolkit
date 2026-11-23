package application

import (
	"context"
	"strings"
	"testing"
)

func TestJWXKPlanPersistenceIsolationAndRevision(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(context.Background(), account); err != nil {
			t.Fatal(err)
		}
	}
	clients := map[string]*jwxkSelectedClient{}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: account}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	clients["student-a"].Close()
	read, save := "/api/course-selection/jwxk/plan/read", "/api/course-selection/jwxk/plan/save"
	response := apiRequest(handler, "POST", read, `{"batch_code":"round"}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"revision":0`) {
		t.Fatal(response)
	}
	body := `{"batch_code":"round","term_code":"term","revision":0,"groups":[{"group_id":"group","name":"方案组","target_count":1}],"items":[{"class_id":"class","course_code":"C1","course_name":"课程","priority":2,"secretVal":"must-not-save","schedules":[{"weeks":[1,2],"weekday":1,"start_section":1,"end_section":2,"secretVal":"must-not-save"}]}],"experiment_selections":{"class":["exp","exp"]}}`
	response = apiRequest(handler, "POST", save, body, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"revision":1`) || !strings.Contains(response.Body.String(), `"utility":9`) || strings.Contains(response.Body.String(), "must-not-save") || strings.Contains(response.Body.String(), `"priority"`) {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", save, body, cookieA); response.Code != 409 {
		t.Fatal("stale write accepted", response)
	}
	response = apiRequest(handler, "POST", read, `{"batch_code":"round"}`, cookieA)
	if !strings.Contains(response.Body.String(), `"plan_group_id":"C1"`) || !strings.Contains(response.Body.String(), `"experiment_selections":{"class":["exp"]}`) || !strings.Contains(response.Body.String(), `"weeks":[1,2]`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", read, `{"batch_code":"round"}`, cookieB)
	if strings.Contains(response.Body.String(), `"course_code":"C1"`) || !strings.Contains(response.Body.String(), `"revision":0`) {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", read, `{"batch_code":"round"}`, nil); response.Code != 401 {
		t.Fatal(response)
	}
	if clients["student-a"].calls != 0 || clients["student-b"].calls != 0 {
		t.Fatal("plan access contacted school")
	}
}
