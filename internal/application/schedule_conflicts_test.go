package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type conflictClient struct {
	testCampusClient
	calls   int
	failure string
}

func (client *conflictClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	client.calls++
	if client.failure == "auth" {
		return nil, &campus.Error{Code: "AUTH_EXPIRED", Message: "请重新登录"}
	}
	if client.failure != "" {
		return nil, errors.New("synthetic unavailable")
	}
	if !strings.HasSuffix(target, "getMyScheduleDetail.do") || form.Get("XNXQDM") != "2026-2027-1" || form.Get("XQDM") != "" || form.Has("ZC") {
		return nil, errors.New("wrong conflict baseline request")
	}
	return []byte(`{"code":"0","datas":{"arrangedList":[{"courseName":"官方基线","courseCode":"BASE","teachClassId":"class-b","weeks":[1,2],"dayOfWeek":1,"beginSection":1,"endSection":2}],"notArrangeList":[],"practiceList":[]}}`), nil
}

func TestScheduleConflictCacheLiveFailureAndIsolation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	api.database.AddAccount(context.Background(), "student-b")
	clients := map[string]*conflictClient{}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &conflictClient{testCampusClient: testCampusClient{account: account}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	session := store.Session{Account: "student-a", Generation: 1}
	cache := store.CacheEntry{Resource: "personal-timetable", Variant: "term:2026-2027-1", SchemaVersion: 3, Revision: "snapshot", Payload: json.RawMessage(`{"term_code":"2026-2027-1","courses":[{"meeting_id":"b","course_name":"缓存基线","course_code":"BASE","teaching_class_id":"class-b","weeks":[1,2],"weekday":1,"start_section":1,"end_section":2}]}`)}
	if _, err := api.database.CommitCache(context.Background(), session, cache, "test", nil); err != nil {
		t.Fatal(err)
	}
	body := `{"term_code":"2026-2027-1","candidates":[{"candidate_id":"c","course_name":"候选","course_code":"NEW","weeks":[1],"weekday":1,"start_section":1,"end_section":2}]}`
	endpoint := "/api/schedule/conflicts/check"
	response := apiRequest(handler, "POST", endpoint, body, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"conflict"`) || clients["student-a"].calls != 0 {
		t.Fatal(response.Code, response.Body.String())
	}
	response = apiRequest(handler, "POST", endpoint, body, cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"baseline_available":false`) || strings.Contains(response.Body.String(), "缓存基线") {
		t.Fatal("cross-account baseline", response.Body.String())
	}
	api.database.InvalidateCache(context.Background(), session, "personal-timetable", "term:2026-2027-1")
	response = apiRequest(handler, "POST", endpoint, body, cookieA)
	var stale struct {
		Results []struct {
			Status string `json:"status"`
		} `json:"results"`
	}
	if json.Unmarshal(response.Body.Bytes(), &stale) != nil || len(stale.Results) != 1 || stale.Results[0].Status != "unknown" {
		t.Fatal(response.Body.String())
	}
	live := strings.Replace(body, `"term_code"`, `"resolve_personal_timetable":true,"term_code"`, 1)
	response = apiRequest(handler, "POST", endpoint, live, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"baseline_stale":false`) || !strings.Contains(response.Body.String(), "官方基线") || strings.Contains(response.Body.String(), "缓存基线") {
		t.Fatal(response.Code, response.Body.String())
	}
	clients["student-a"].failure = "network"
	response = apiRequest(handler, "POST", endpoint, live, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"baseline_available":false`) || strings.Contains(response.Body.String(), "缓存基线") {
		t.Fatal(response.Code, response.Body.String())
	}
	clients["student-a"].failure = "auth"
	response = apiRequest(handler, "POST", endpoint, live, cookieA)
	if response.Code != 401 {
		t.Fatal("lost authentication hidden", response.Code, response.Body.String())
	}
}
