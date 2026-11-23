package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type experimentTestClient struct {
	testCampusClient
	calls int
	fail  bool
}

func (client *experimentTestClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	client.calls++
	if client.fail {
		return nil, errors.New("upstream unavailable")
	}
	switch {
	case strings.HasSuffix(target, "queryAcademicYearSemester.do"):
		return []byte(`{"code":0,"datas":{"queryAcademicYearSemester":"2026-2027-1"}}`), nil
	case strings.HasSuffix(target, "queryCanSelectedCourses.do"):
		return []byte(`{"code":0,"datas":{"queryCanSelectedCourses":[]}}`), nil
	case strings.HasSuffix(target, "queryStudentAllSelectCourseResult.do"):
		return []byte(`{"code":0,"datas":{"queryStudentAllSelectCourseResult":[{"taskId":"task","courseName":"官方实验","projectResultList":[{"projectCode":"project","roundId":"round"}]}]}}`), nil
	case strings.HasSuffix(target, "queryTaskProjectRounds.do"):
		return []byte(`{"code":0,"datas":{"queryTaskProjectRounds":[{"wid":"round","roundName":"实验班","classWeeks":"1-8周","classDays":"星期一","classSessions":"1-2节","courseCapacity":20,"selectedNums":1}]}}`), nil
	}
	return nil, errors.New("unexpected endpoint")
}

func TestExperimentCacheIsolationFailurePreservationAndRounds(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	api.database.AddAccount(ctx, "student-b")
	client := &experimentTestClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(account string) (CampusClient, error) {
		if account == "student-a" {
			return client, nil
		}
		return &testCampusClient{account: account}, nil
	}
	cookie := loginCookie(t, handler, "student-a")
	other := loginCookie(t, handler, "student-b")
	session, _ := api.database.Session(ctx, cookie.Value)
	response := apiRequest(handler, "POST", "/api/cache/refresh/experiment-courses", `{"force":true}`, cookie)
	if response.Code != 200 && response.Code != 202 {
		t.Fatal(response.Code, response.Body.String())
	}
	var job map[string]any
	json.Unmarshal(response.Body.Bytes(), &job)
	if state := waitTestJob(t, api.resources, session, job); state.Status != "completed" {
		t.Fatal(state)
	}
	before := client.calls
	for _, path := range []string{"/api/experiment-courses/cache", "/api/offline/experiment-courses"} {
		response := apiRequest(handler, "GET", path, "", cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), "官方实验") || client.calls != before {
			t.Fatal("cache read did school work", response)
		}
		response = apiRequest(handler, "GET", path, "", other)
		if response.Code != 200 || strings.Contains(response.Body.String(), "官方实验") {
			t.Fatal("cross-account leak", response)
		}
	}
	entry := store.CacheEntry{Resource: "personal-timetable", Variant: "term:2026-2027-1", SchemaVersion: 1, Revision: "baseline", Payload: json.RawMessage(`{"term_code":"2026-2027-1","courses":[{"course_name":"高等数学","weeks":[1,2],"weekday":1,"start_section":2,"end_section":3}]}`)}
	if _, err := api.database.CommitCache(ctx, session, entry, "test", nil); err != nil {
		t.Fatal(err)
	}
	path := "/api/experiment-courses/task/rounds?course_no=course&project_code=project&term=2026-2027-1"
	response = apiRequest(handler, "GET", path, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"disabled_reason":"与个人课表冲突"`) {
		t.Fatal(response)
	}
	if err := api.database.InvalidateCache(ctx, session, "personal-timetable", "term:2026-2027-1"); err != nil {
		t.Fatal(err)
	}
	response = apiRequest(handler, "GET", path, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"can_select":true`) || !strings.Contains(response.Body.String(), `"conflict_status":"unknown"`) {
		t.Fatal(response)
	}
	client.fail = true
	api.resources.mutex.Lock()
	for _, previous := range api.resources.jobs {
		previous.UpdatedAt = time.Now().Add(-3 * time.Second)
	}
	api.resources.mutex.Unlock()
	result, err := api.resources.Submit(ctx, session, "experiment-courses", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	if state := waitTestJob(t, api.resources, session, result); state.Status != "failed" {
		t.Fatal(state)
	}
	response = apiRequest(handler, "GET", "/api/experiment-courses/cache", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "官方实验") {
		t.Fatal("failed refresh lost cached results")
	}
	if response := apiRequest(handler, "GET", "/api/experiment-courses", "", cookie); response.Code == 200 {
		t.Fatal("failed live read became empty success")
	}
}
