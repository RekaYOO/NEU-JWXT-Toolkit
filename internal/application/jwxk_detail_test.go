package application

import (
	"context"
	"strings"
	"testing"
)

func TestJWXKDetailHTTPIdentityAndValidation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/catalog/detail"
	body := `{"batch_code":"round","teaching_class_type":"FANKC","course_code":"course","class_id":"class"}`
	if response := apiRequest(handler, "POST", endpoint, body, nil); response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", endpoint, strings.Replace(body, `"course_code":"course"`, `"course_code":"../bad"`, 1), cookie); response.Code != 422 || client.calls != 0 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || client.calls != 4 || response.Header().Get("Cache-Control") != "no-store" || !strings.Contains(response.Body.String(), `"class_id":"class"`) {
		t.Fatal(response, client.calls)
	}
}
