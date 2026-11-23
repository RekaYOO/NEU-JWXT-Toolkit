package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestOutlineMetadataReadDoesNotFetchAndRejectsInvalidCodes(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	payload := json.RawMessage(`{"course_code":"TEST","grading_scale":"两级制","status":"success"}`)
	if _, err := api.database.CommitCache(context.Background(), session, store.CacheEntry{Resource: "course-outline-metadata", Variant: "course:TEST", SchemaVersion: 2, Revision: "metadata", Payload: payload}, "test", nil); err != nil {
		t.Fatal(err)
	}
	response := apiRequest(handler, "POST", "/api/course-outlines/metadata/read", `{"course_codes":["TEST","TEST","MISSING"]}`, cookie)
	var result struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil || len(result.Items) != 1 || result.Items[0]["needs_sync"] != false {
		t.Fatalf("metadata read: %s %v", response.Body.String(), err)
	}
	if len(api.resources.jobs) != 0 {
		t.Fatal("cache read started network job")
	}
	response = apiRequest(handler, "POST", "/api/course-outlines/metadata/read", `{"course_codes":["../../private"]}`, cookie)
	if response.Code != 422 {
		t.Fatal("invalid course accepted")
	}
	response = apiRequest(handler, "POST", "/api/course-outlines/metadata/read", `{"course_codes":["TEST"]}`, nil)
	if response.Code != 401 || strings.Contains(response.Body.String(), "两级制") {
		t.Fatal("anonymous metadata access")
	}
}

func TestMetadataFreshnessAndCancellation(t *testing.T) {
	entry := store.CacheEntry{SchemaVersion: 2, CheckedAt: time.Now().Add(-8 * 24 * time.Hour).Format(time.RFC3339Nano)}
	if !metadataStale(entry, map[string]any{"status": "not_found"}) || metadataStale(entry, map[string]any{"status": "success"}) {
		t.Fatal("negative cache lifetime not enforced")
	}
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	started := make(chan struct{})
	api.resources.Register("course-outline-metadata", ResourceSpec{Fetch: func(ctx context.Context, _ *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		close(started)
		<-ctx.Done()
		return nil, nil, ctx.Err()
	}})
	response := apiRequest(handler, "POST", "/api/course-outlines/metadata/sync", `{"courses":[{"course_code":"TEST"}]}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("sync not started")
	}
	response = apiRequest(handler, "POST", "/api/course-outlines/metadata/sync", `{"courses":[{"course_code":"OTHER"}]}`, cookie)
	if !strings.Contains(response.Body.String(), `"accepted":false`) {
		t.Fatal("concurrent batch replaced active work")
	}
	response = apiRequest(handler, "POST", "/api/course-outlines/metadata/sync/cancel", `{}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	session, _ := api.database.Session(context.Background(), cookie.Value)
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if api.outlineState(session).snapshot(session.Account)["running"] == false {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("cancel blocked behind remote request")
}
