package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestTimetableBootstrapAndPersonalReadAreCacheOnly(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	for _, entry := range []store.CacheEntry{{Resource: "timetable-index", Variant: "default", SchemaVersion: 1, Revision: "index", Payload: json.RawMessage(`{"terms":[{"code":"2026-2027-1","current":true}],"current":"2026-2027-1"}`)}, {Resource: "personal-timetable", Variant: "term:2026-2027-1", SchemaVersion: 3, Revision: "personal", Payload: json.RawMessage(`{"term_code":"2026-2027-1","campuses":[],"weeks":[],"sections_by_campus":{},"courses":[],"unscheduled":[],"practices":[]}`)}} {
		if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	for _, path := range []string{"/api/timetable/bootstrap", "/api/client/bootstrap", "/api/timetable/terms", "/api/timetable/personal?term_code=2026-2027-1"} {
		response := apiRequest(handler, "GET", path, "", cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), "2026-2027-1") {
			t.Fatalf("%s: %d %s", path, response.Code, response.Body.String())
		}
	}
	if len(api.resources.jobs) != 0 {
		t.Fatal("fresh snapshot triggered upstream work")
	}
	if response := apiRequest(handler, "GET", "/api/timetable/bootstrap", "", nil); response.Code != 401 {
		t.Fatal("anonymous personal timetable access")
	}
}

func TestTimetableAllowedTermsRequireExplicitCurrent(t *testing.T) {
	index := map[string]any{"current": "2026-2027-1", "terms": []map[string]any{{"code": "2025-2026-2"}, {"code": "2026-2027-2"}, {"code": "2026-2027-1", "current": true}}}
	terms := timetableAllowed(index)
	if len(terms) != 2 || terms[0] != "2026-2027-1" || terms[1] != "2026-2027-2" {
		t.Fatal(terms)
	}
	index["current"] = "missing"
	if len(timetableAllowed(index)) != 0 {
		t.Fatal("unconfirmed term allowed")
	}
}
