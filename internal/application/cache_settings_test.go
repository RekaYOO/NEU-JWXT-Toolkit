package application

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestCacheSettingsAccountIsolationValidationAndFreshness(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	cookie := loginCookie(t, handler, "student-a")
	other := loginCookie(t, handler, "student-b")
	session, _ := api.database.Session(ctx, cookie.Value)
	if response := apiRequest(handler, "GET", "/api/system-settings/cache", "", nil); response.Code != 401 {
		t.Fatal(response.Code)
	}
	response := apiRequest(handler, "GET", "/api/system-settings/cache", "", cookie)
	var defaults struct {
		Cache map[string]store.CachePolicy `json:"cache"`
	}
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &defaults) != nil || defaults.Cache["scores"].IntervalMinutes != 5 {
		t.Fatal(response.Body.String())
	}
	for _, body := range []string{
		`{"resources":{"scores":{"enabled":true,"interval_minutes":0}}}`,
		`{"resources":{"scores":{"enabled":true,"interval_minutes":52560001}}}`,
		`{"resources":{"scores":{"enabled":true,"interval_minutes":1.5}}}`,
		`{"resources":{"scores":{"enabled":"false","interval_minutes":5}}}`,
		`{"resources":{"scores":{"enabled":false}}}`,
		`{"resources":{"scores":null}}`,
		`{"resources":{"scores":{"enabled":true,"interval_minutes":5,"account":"student-b"}}}`,
	} {
		if result := apiRequest(handler, "PUT", "/api/system-settings/cache", body, cookie); result.Code != 422 {
			t.Fatalf("%s: %d %s", body, result.Code, result.Body.String())
		}
	}
	if result := apiRequest(handler, "PUT", "/api/system-settings/cache", `{"resources":{"unknown":{"enabled":true,"interval_minutes":5}}}`, cookie); result.Code != 400 {
		t.Fatal(result.Body.String())
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if result := apiRequest(handler, method, "/api/documents/settings/cache", `{"revision":0,"data":{}}`, cookie); result.Code != 403 {
			t.Fatal("generic bypass", result.Code)
		}
	}
	save := func(body string) {
		t.Helper()
		if result := apiRequest(handler, "PUT", "/api/system-settings/cache", body, cookie); result.Code != 200 {
			t.Fatal(result.Code, result.Body.String())
		}
	}
	checked := time.Now().Add(-10 * time.Minute).UTC().Format(time.RFC3339Nano)
	_, err := api.database.CommitCache(ctx, session, store.CacheEntry{Resource: "scores", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"scores":[]}`), Revision: "baseline", PreserveFreshness: true, CheckedAt: checked, AttemptedAt: checked}, "test", nil)
	if err != nil {
		t.Fatal(err)
	}
	save(`{"resources":{"scores":{"enabled":true,"interval_minutes":60}}}`)
	entry, err := api.database.ReadCache(ctx, session, "scores", "default")
	if err != nil || entry.Stale(scoresTTL) || entry.Metadata(scoresTTL)["is_stale"] != false {
		t.Fatal("policy ignored", err)
	}
	result, err := api.resources.Submit(ctx, session, "scores", "default", "page_swr", false)
	if err != nil || result["status"] != "fresh" {
		t.Fatal("coordinator ignored interval", result, err)
	}
	otherResponse := apiRequest(handler, "GET", "/api/system-settings/cache", "", other)
	json.Unmarshal(otherResponse.Body.Bytes(), &defaults)
	if defaults.Cache["scores"].IntervalMinutes != 5 {
		t.Fatal("policy leaked between accounts")
	}
	save(`{"resources":{"avatar":{"enabled":true,"interval_minutes":30}}}`)
	policies, _, err := api.database.CachePolicies(ctx, session.Account)
	if err != nil || policies["scores"].IntervalMinutes != 60 {
		t.Fatal("partial update lost settings", err)
	}
	save(`{"resources":{"scores":{"enabled":false,"interval_minutes":52560000}}}`)
	entry, err = api.database.ReadCache(ctx, session, "scores", "default")
	if err != nil || !entry.Stale(scoresTTL) || string(entry.Payload) != `{"scores":[]}` {
		t.Fatal("disabled freshness lost snapshot", err)
	}
	apiRequest(handler, "POST", "/api/logout", "", cookie)
	if result := apiRequest(handler, "PUT", "/api/system-settings/cache", `{"resources":{}}`, cookie); result.Code != 401 {
		t.Fatal("revoked session saved settings")
	}
}

func TestOutlineNegativeCacheKeepsSevenDayLimitWithPolicy(t *testing.T) {
	entry := store.CacheEntry{SchemaVersion: 2, CheckedAt: time.Now().Add(-8 * 24 * time.Hour).UTC().Format(time.RFC3339Nano), Policy: &store.CachePolicy{Enabled: true, IntervalMinutes: 43200}}
	if !metadataStale(entry, map[string]any{"status": "not_found"}) {
		t.Fatal("negative cache exceeded seven days")
	}
	if metadataStale(entry, map[string]any{"status": "success"}) {
		t.Fatal("negative limit mutated positive policy")
	}
}
