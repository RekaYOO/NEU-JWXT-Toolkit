package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestExplicitLogoutCleanupIsAccountScoped(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	for _, token := range []string{cookieA.Value, cookieB.Value} {
		session, err := api.database.Session(ctx, token)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := api.database.CommitCache(ctx, session, store.CacheEntry{Resource: "scores", Variant: "default", SchemaVersion: 1, Revision: "before", Payload: json.RawMessage(`{"scores":[]}`)}, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	result := apiRequest(handler, "POST", "/api/logout?clear_data=true", "", cookieA)
	if result.Code != 200 || !strings.Contains(result.Body.String(), `"data_cleared":true`) {
		t.Fatal(result.Code, result.Body.String())
	}
	cookieA = loginCookie(t, handler, "student-a")
	sessionA, err := api.database.Session(ctx, cookieA.Value)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := api.database.ReadCache(ctx, sessionA, "scores", "default"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("cleared cache returned", err)
	}
	sessionB, err := api.database.Session(ctx, cookieB.Value)
	if err != nil {
		t.Fatal(err)
	}
	if entry, err := api.database.ReadCache(ctx, sessionB, "scores", "default"); err != nil || entry.Revision != "before" {
		t.Fatal("other account changed", err)
	}
}

func TestOfflineReadsAfterLogoutNeverRefreshOrWrite(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range []store.CacheEntry{{Resource: "scores", Variant: "default", SchemaVersion: 1, Revision: "scores-1", Payload: json.RawMessage(`{"scores":[],"overall_gpa":null,"official_major_rank":42}`)}, {Resource: "academic-report", Variant: "default", SchemaVersion: 1, Revision: "report-1", Payload: json.RawMessage(`{"student_id":"20250001","categories":[],"credit_summary":{}}`)}} {
		if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	if response := apiRequest(handler, "POST", "/api/logout", "", cookie); response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	for _, path := range []string{"/api/offline/scores", "/api/offline/academic-report?refresh=true"} {
		response := apiRequest(handler, "GET", path, "", nil)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"source":"offline"`) || !strings.Contains(response.Body.String(), `"is_fresh":false`) {
			t.Fatalf("offline read %s: %d %s", path, response.Code, response.Body.String())
		}
		if path == "/api/offline/scores" && !strings.Contains(response.Body.String(), `"official_major_rank":42`) {
			t.Fatal("offline score response lost the cached rank")
		}
	}
	response := apiRequest(handler, "GET", "/api/offline/status", "", nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"available":true`) {
		t.Fatal(response.Body.String())
	}
	if response := apiRequest(handler, "PUT", "/api/offline/gpa-policy", `{"mode":"from_2025"}`, nil); response.Code != 405 {
		t.Fatalf("offline mutation: %d", response.Code)
	}
	if len(api.resources.jobs) != 0 {
		t.Fatal("offline reads created remote jobs")
	}
}

func TestRemoteOfflineDoesNotChooseAnotherAccount(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "20250001"); err != nil {
		t.Fatal(err)
	}
	cookie := loginCookie(t, handler, "20250001")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := api.database.CommitCache(context.Background(), session, store.CacheEntry{Resource: "scores", Variant: "default", SchemaVersion: 1, Revision: "private", Payload: json.RawMessage(`{"scores":[]}`)}, "test", nil); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/api/offline/status", "/api/offline/scores", "/api/offline/gpa-policy"} {
		response := apiRequest(handler, "GET", path, "", nil)
		if response.Code != 403 || strings.Contains(response.Body.String(), "20250001") {
			t.Fatalf("anonymous remote offline read: %d %s", response.Code, response.Body.String())
		}
	}
}
