package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type evaluationWriteClient struct {
	testCampusClient
	writes    int
	unknown   bool
	notSent   bool
	completed bool
	second    bool
	cancel    context.CancelFunc
	last      json.RawMessage
}

func (client *evaluationWriteClient) EvaluationRequest(_ context.Context, path string, body json.RawMessage) ([]byte, error) {
	if strings.HasSuffix(path, "saveJdp") {
		if client.notSent {
			return nil, &campus.Error{Code: "EVALUATION_NOT_SENT", Message: "connection not established"}
		}
		client.writes++
		client.last = append(json.RawMessage{}, body...)
		if client.cancel != nil {
			client.cancel()
		}
		if client.unknown {
			return nil, errors.New("lost response")
		}
		client.completed = true
		return []byte(`"success"`), nil
	}
	if strings.HasSuffix(path, "findCycle") {
		return []byte(`[{"value":"2025-2026-2","isdefault":1}]`), nil
	}
	if strings.HasSuffix(path, "findJdpXqByTaskid") {
		status := "1"
		if client.completed {
			status = "0"
		}
		rows := []any{map[string]any{"taskid": "task", "xspjid": "record", "issubmit": status, "iskpj": "1", "libid": "library", "pjrid": client.account, "kcmc": "测试课程"}}
		if client.second {
			rows = append(rows, map[string]any{"taskid": "task", "xspjid": "second", "issubmit": "1", "iskpj": "1", "libid": "library", "pjrid": client.account})
		}
		return json.Marshal(map[string]any{"rowCount": len(rows), "items": rows})
	}
	if strings.Contains(path, "getTasklib/") {
		return []byte(`[{"id":"library"}]`), nil
	}
	return []byte(`{"list":[{"zbid":"first","evaltype":1,"sfbt":1,"leveljson":[{"value":5,"df":90},{"value":6,"df":100}]},{"zbid":"second","evaltype":1,"sfbt":1,"leveljson":[{"value":5,"df":90},{"value":6,"df":100}]},{"zbid":"text","evaltype":2}]}`), nil
}

func setupEvaluationWrite(t *testing.T) (*API, http.Handler, *evaluationWriteClient, *http.Cookie) {
	t.Helper()
	api, handler := testApplication(t, "local-single")
	client := &evaluationWriteClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	return api, handler, client, cookie
}

func TestEvaluationPreviewAndSubmitOnlyOnce(t *testing.T) {
	api, handler, client, cookie := setupEvaluationWrite(t)
	response := apiRequest(handler, "POST", "/api/evaluation/submit", `{"task_id":"task","xspjid":"record","text_results":{"text":"private feedback"}}`, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"dry_run":true`) || client.writes != 0 || strings.Contains(response.Body.String(), "private feedback") {
		t.Fatal(response)
	}
	session, _ := api.database.Session(context.Background(), cookie.Value)
	key := evaluationOperationKey("2025-2026-2", "task", "record")
	if _, err := api.database.EvaluationAttempt(context.Background(), session, key); err == nil {
		t.Fatal("preview created an attempt")
	}
	for attempt := 0; attempt < 2; attempt++ {
		response := apiRequest(handler, "POST", "/api/evaluation/submit", `{"task_id":"task","xspjid":"record","dry_run":false,"text_results":{"text":"private feedback"}}`, cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"success":true`) || strings.Contains(response.Body.String(), "private feedback") {
			t.Fatal(response)
		}
	}
	if client.writes != 1 || !strings.Contains(string(client.last), "private feedback") {
		t.Fatal("duplicate or lost text")
	}
}

func TestEvaluationUnknownBlocksReplayUntilOfficialConfirmation(t *testing.T) {
	_, handler, client, cookie := setupEvaluationWrite(t)
	client.unknown = true
	for attempt := 0; attempt < 2; attempt++ {
		response := apiRequest(handler, "POST", "/api/evaluation/submit", `{"task_id":"task","xspjid":"record","dry_run":false}`, cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"unknown":true`) {
			t.Fatal(response)
		}
	}
	if client.writes != 1 {
		t.Fatal("unknown write repeated")
	}
	client.completed = true
	response := apiRequest(handler, "POST", "/api/evaluation/submit", `{"task_id":"task","xspjid":"record","dry_run":false}`, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"success":true`) || client.writes != 1 {
		t.Fatal("official confirmation not recovered")
	}
}

func TestEvaluationConfirmedNotSentCanRetryAfterRecovery(t *testing.T) {
	_, handler, client, cookie := setupEvaluationWrite(t)
	client.notSent = true
	response := apiRequest(handler, "POST", "/api/evaluation/submit", `{"task_id":"task","xspjid":"record","dry_run":false}`, cookie)
	if response.Code != 200 || client.writes != 0 || !strings.Contains(response.Body.String(), `"state":"not_sent"`) {
		t.Fatal(response)
	}
	client.notSent = false
	response = apiRequest(handler, "POST", "/api/evaluation/submit", `{"task_id":"task","xspjid":"record","dry_run":false}`, cookie)
	if response.Code != 200 || client.writes != 1 || !strings.Contains(response.Body.String(), `"success":true`) {
		t.Fatal(response)
	}
}

func TestEvaluationBatchStopsAfterUnknownAndRejectsForeignSelection(t *testing.T) {
	_, handler, client, cookie := setupEvaluationWrite(t)
	client.unknown = true
	client.second = true
	response := apiRequest(handler, "POST", "/api/evaluation/batch", `{"task_id":"task","xspjids":["foreign"],"dry_run":false,"delay":0}`, cookie)
	if response.Code != 422 || client.writes != 0 {
		t.Fatal("foreign selection dispatched")
	}
	response = apiRequest(handler, "POST", "/api/evaluation/batch", `{"task_id":"task","xspjids":["record","second"],"dry_run":false,"delay":0}`, cookie)
	if response.Code != 200 || client.writes != 1 || !strings.Contains(response.Body.String(), `"remaining_count":1`) || !strings.Contains(response.Body.String(), `"stopped":true`) {
		t.Fatal(response)
	}
}

func TestEvaluationBatchRuntimeCancellationStopsLaterCourses(t *testing.T) {
	api, handler, client, cookie := setupEvaluationWrite(t)
	client.second = true
	session, _ := api.database.Session(context.Background(), cookie.Value)
	client.cancel = api.accounts.Runtime(session).Cancel
	response := apiRequest(handler, "POST", "/api/evaluation/batch", `{"task_id":"task","dry_run":false,"delay":0}`, cookie)
	if response.Code != 200 || client.writes != 1 || !strings.Contains(response.Body.String(), `"remaining_count":1`) {
		t.Fatal(response)
	}
}

func TestEvaluationWriteValidation(t *testing.T) {
	_, handler, client, cookie := setupEvaluationWrite(t)
	for _, body := range []string{`{"task_id":"task","xspjid":"record","unexpected":true}`, `{"task_id":"task","xspjid":"record","custom_scores":{"first":7}}`, `{"task_id":"task","xspjid":"record","strategy":"custom","custom_scores":{"foreign":5},"dry_run":false}`} {
		response := apiRequest(handler, "POST", "/api/evaluation/submit", body, cookie)
		if response.Code != 422 || client.writes != 0 {
			t.Fatal(response)
		}
	}
}
