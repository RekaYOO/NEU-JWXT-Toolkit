package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestJWXKCatalogLocalMissNeverContactsSchool(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/catalog/search"
	response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round","local_only":true}`, cookie)
	if response.Code != 200 || client.calls != 0 || !strings.Contains(response.Body.String(), `"cache_hit":false`) || !strings.Contains(response.Body.String(), `"data_source":"local"`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, `{"batch_code":"round","local_only":true}`, nil)
	if response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, `{"batch_code":"round","time_slot":{"weekday":8,"section":1}}`, cookie)
	if response.Code != 422 || client.calls != 0 {
		t.Fatal(response)
	}
}

type jwxkCatalogClient struct{ jwxkSelectedClient }

func waitJWXKScan(t *testing.T, api *API, body string) {
	t.Helper()
	var payload map[string]any
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		t.Fatal(err)
	}
	id := academic.Text(payload["sync_job_id"])
	if id == "" {
		return
	}
	api.resources.mutex.Lock()
	job := api.resources.jobs[id]
	api.resources.mutex.Unlock()
	if job == nil {
		t.Fatal("missing background scan job")
	}
	select {
	case <-job.done:
	case <-time.After(5 * time.Second):
		t.Fatal("scan did not finish")
	}
}

func (client *jwxkCatalogClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if input.Path == "/xsxk/web/now" {
		client.calls++
		return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
	}
	if input.Path == "/xsxk/web/studentInfo" {
		client.calls++
		return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","name":"课程轮次","typeCode":"02","clazzTypeList":["FANKC"]}]}}}`), nil
	}
	return client.jwxkSelectedClient.RequestService(ctx, input)
}

func TestJWXKQuerySnapshotPersistsAndIsolatesAccounts(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	clients := map[string]*jwxkCatalogClient{}
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(context.Background(), account); err != nil {
			t.Fatal(err)
		}
	}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkCatalogClient{jwxkSelectedClient: jwxkSelectedClient{testCampusClient: testCampusClient{account: account}}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	endpoint := "/api/course-selection/jwxk/catalog/search"
	response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"class_id":"class"`) {
		t.Fatal(response)
	}
	waitJWXKScan(t, api, response.Body.String())
	before := clients["student-a"].calls
	clients["student-a"].Close()
	response = apiRequest(handler, "POST", endpoint, `{"batch_code":"round","local_only":true,"filters":{}}`, cookieA)
	if response.Code != 200 || clients["student-a"].calls != before || !strings.Contains(response.Body.String(), `"cache_hit":true`) || !strings.Contains(response.Body.String(), `"class_id":"class"`) || !strings.Contains(response.Body.String(), `"catalog_complete":true`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, `{"batch_code":"round","local_only":true}`, cookieB)
	if response.Code != 200 || clients["student-b"].calls != 0 || !strings.Contains(response.Body.String(), `"cache_hit":false`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, `{"batch_code":"round","local_only":true,"page_number":2}`, cookieA)
	if !strings.Contains(response.Body.String(), `"cache_hit":true`) || !strings.Contains(response.Body.String(), `"groups":[]`) {
		t.Fatal("archive pagination failed", response)
	}
}

func TestJWXKSnapshotDeletionAndRevocationRejectLateRefresh(t *testing.T) {
	api, _ := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	input := academic.JWXKCatalogSearch{Batch: "round", Scope: "ALL", Page: 1, Size: 20, OnlineMode: "all"}
	key := jwxkQueryKey(input)
	payload := map[string]any{"groups": []any{}, "total": 0}
	if err := api.saveJWXKQuerySnapshot(ctx, session, key, payload, 0); err != nil {
		t.Fatal(err)
	}
	document, _, err := api.jwxkQuerySnapshot(ctx, session, key)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := api.database.SaveForSession(ctx, session, jwxkQueryNamespace, key, []byte("null"), document.Revision, true); err != nil {
		t.Fatal(err)
	}
	if err := api.saveJWXKQuerySnapshot(ctx, session, key, payload, document.Revision); !errors.Is(err, store.ErrConflict) {
		t.Fatal("late refresh resurrected deletion", err)
	}
	_, snapshot, err := api.jwxkQuerySnapshot(ctx, session, key)
	if err != nil || snapshot != nil {
		t.Fatal(snapshot, err)
	}
	if err := api.database.SetEnabled(ctx, "student-a", false); err != nil {
		t.Fatal(err)
	}
	if err := api.saveJWXKQuerySnapshot(ctx, session, key, payload, 2); !errors.Is(err, store.ErrAccess) {
		t.Fatal(err)
	}
}

func TestJWXKQuerySnapshotSurvivesDatabaseReopen(t *testing.T) {
	directory := t.TempDir()
	database, err := store.Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := database.AddAccount(ctx, "student-a"); err != nil {
		t.Fatal(err)
	}
	generation, err := database.Generation(ctx, "student-a")
	if err != nil {
		t.Fatal(err)
	}
	session := store.Session{Account: "student-a", Generation: generation}
	api := &API{database: database}
	if err := api.saveJWXKQuerySnapshot(ctx, session, "round-query", map[string]any{"total": 1, "groups": []any{map[string]any{"course_code": "CS1"}}}, 0); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	database, err = store.Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	api.database = database
	document, payload, err := api.jwxkQuerySnapshot(ctx, session, "round-query")
	if err != nil || document.Revision != 1 || academic.Number(payload["total"]) != 1 {
		t.Fatal(document, payload, err)
	}
}
