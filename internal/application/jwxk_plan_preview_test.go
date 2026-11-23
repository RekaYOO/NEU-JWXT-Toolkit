package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestJWXKPlanPreviewUsesOnlyAccountTimetableCache(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
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
	generation, _ := api.database.Generation(ctx, "student-a")
	payload := json.RawMessage(`{"term_code":"term","courses":[{"courseCode":"A","courseName":"原课程","weeks":[1,2],"dayOfWeek":1,"beginSection":1,"endSection":2}]}`)
	if _, err := api.database.CommitCache(ctx, store.Session{Account: "student-a", Generation: generation}, store.CacheEntry{Resource: "personal-timetable", Variant: "term:term", SchemaVersion: 3, Payload: payload, Revision: "revision"}, "test", nil); err != nil {
		t.Fatal(err)
	}
	clients["student-a"].Close()
	body := `{"batch_code":"round","term_code":"term","meetings":[{"course_code":"B","course_name":"候选","weeks":[2],"weekday":1,"start_section":2,"end_section":3}]}`
	endpoint := "/api/course-selection/jwxk/plan/preview"
	response := apiRequest(handler, "POST", endpoint, body, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"conflict"`) || !strings.Contains(response.Body.String(), `"baseline_available":true`) || clients["student-a"].calls != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, body, cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"unknown"`) || !strings.Contains(response.Body.String(), `"baseline_available":false`) || clients["student-b"].calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", endpoint, body, nil); response.Code != 401 {
		t.Fatal(response)
	}
}
