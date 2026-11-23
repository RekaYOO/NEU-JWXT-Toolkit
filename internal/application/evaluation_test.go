package application

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type evaluationTestClient struct {
	testCampusClient
	calls  atomic.Int32
	failed bool
}

func (client *evaluationTestClient) EvaluationRequest(_ context.Context, path string, _ json.RawMessage) ([]byte, error) {
	client.calls.Add(1)
	if client.failed {
		return nil, &campus.Error{Code: "EVALUATION_AUTH_REQUIRED", Message: "评教登录失效"}
	}
	if strings.HasSuffix(path, "findCycle") {
		return []byte(`[{"value":"2025-2026-2","isdefault":1}]`), nil
	}
	if strings.HasSuffix(path, "findJdpXqByTaskidHZ") {
		return []byte(`{"rowCount":1,"items":[{"taskid":"task","taskname":"评教任务","count":2,"ypcount":1,"wpcount":1}]}`), nil
	}
	return []byte(`{"rowCount":0,"items":[]}`), nil
}

func TestEvaluationHTTPContractsAndAuthentication(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	client := &evaluationTestClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	for _, path := range []string{"/api/evaluation/cycles", "/api/evaluation/tasks", "/api/evaluation/tasks/task/courses", "/api/evaluation/courses/record/indicators?task_id=task"} {
		if response := apiRequest(handler, "GET", path, "", nil); response.Code != 401 {
			t.Fatal(response)
		}
	}
	if client.calls.Load() != 0 {
		t.Fatal("anonymous request reached school")
	}
	cookie := loginCookie(t, handler, "student-a")
	response := apiRequest(handler, "GET", "/api/evaluation/tasks", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"pending_count":1`) || !strings.Contains(response.Body.String(), `"xnxq":"2025-2026-2"`) || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(response)
	}
	before := client.calls.Load()
	if response := apiRequest(handler, "GET", "/api/evaluation/courses/record/indicators", "", cookie); response.Code != 422 || client.calls.Load() != before {
		t.Fatal("missing task was dispatched")
	}
	client.failed = true
	if response := apiRequest(handler, "GET", "/api/evaluation/tasks", "", cookie); response.Code == 200 || !strings.Contains(response.Body.String(), "EVALUATION_AUTH_REQUIRED") {
		t.Fatal("failure presented as empty tasks")
	}
	if active, _ := client.Status(); !active {
		t.Fatal("service error invalidated primary login")
	}
	apiRequest(handler, "POST", "/api/logout", "", cookie)
	if response := apiRequest(handler, "GET", "/api/evaluation/cycles", "", cookie); response.Code != 401 {
		t.Fatal("logout ignored")
	}
}
