package application

import (
	"context"
	"strings"
	"testing"
)

func TestJWXKSelectionScheduleReusesOneSelectedRead(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/schedule"
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, nil); response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || client.calls != 5 || response.Header().Get("Cache-Control") != "no-store" || !strings.Contains(response.Body.String(), `"source":"selected_records_fallback"`) || !strings.Contains(response.Body.String(), `"class_id":"class"`) {
		t.Fatal(response, client.calls)
	}
}
