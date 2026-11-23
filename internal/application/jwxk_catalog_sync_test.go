package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestJWXKSnapshotSyncPreservesPartialReadsAndUpdatesEligibility(t *testing.T) {
	api, _ := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	key := jwxkQueryKey(academic.JWXKCatalogSearch{Batch: "round", Scope: "ALL", Page: 1, Size: 20, OnlineMode: "all"})
	payload := map[string]any{"groups": []any{map[string]any{"classes": []any{
		map[string]any{"class_id": "A", "course_code": "C1", "selected": true, "devoted_weight": 30, "eligibility_status": "unknown"},
		map[string]any{"class_id": "B", "course_code": "C1", "selected": false, "eligibility_status": "unknown"},
		map[string]any{"class_id": "C", "course_code": "C2", "selected": true, "eligibility_status": "unknown"},
	}}}}
	if err := api.saveJWXKQuerySnapshot(ctx, session, key, payload, 0); err != nil {
		t.Fatal(err)
	}
	official := map[string]any{"complete": false, "selection_type_code": "04", "selected": []map[string]any{{"class_id": "C", "course_code": "C2", "current_batch_record": true}}, "volunteered": []map[string]any{{"class_id": "A", "course_code": "C1", "current_batch_record": true, "devoted_weight": 50}}}
	if err := api.syncJWXKSelectedSnapshot(ctx, session, "round", official); err != nil {
		t.Fatal(err)
	}
	document, _, err := api.jwxkQuerySnapshot(ctx, session, key)
	if err != nil || document.Revision != 1 {
		t.Fatal(document, err)
	}
	official["complete"] = true
	if err := api.syncJWXKSelectedSnapshot(ctx, session, "round", official); err != nil {
		t.Fatal(err)
	}
	if err := api.syncJWXKEligibilitySnapshot(ctx, session, "round", map[string]any{"results": []map[string]any{{"class_id": "B", "status": "selectable", "reason": ""}}}); err != nil {
		t.Fatal(err)
	}
	document, snapshot, err := api.jwxkQuerySnapshot(ctx, session, key)
	if err != nil {
		t.Fatal(err)
	}
	group := snapshot["groups"].([]any)[0].(map[string]any)
	rows := group["classes"].([]any)
	if rows[0].(map[string]any)["devoted_weight"] != float64(50) || rows[1].(map[string]any)["course_already_selected"] != true || rows[2].(map[string]any)["selected"] != false || group["selectable_count"] != float64(1) {
		t.Fatal(snapshot)
	}
	before := document.Revision
	if err := api.syncJWXKSelectedSnapshot(ctx, session, "round", official); err != nil {
		t.Fatal(err)
	}
	document, _, _ = api.jwxkQuerySnapshot(ctx, session, key)
	if document.Revision != before {
		t.Fatal("unchanged snapshot rewritten")
	}
}

func TestJWXKSnapshotConcurrentDeletionIsNotRecreated(t *testing.T) {
	api, _ := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	key := "round-" + strings.Repeat("a", 64)
	payload := map[string]any{"groups": []any{map[string]any{"classes": []any{map[string]any{"class_id": "A"}}}}}
	api.saveJWXKQuerySnapshot(ctx, session, key, payload, 0)
	err := api.updateJWXKQueryCourses(ctx, session, "round", func(course map[string]any) {
		course["selected"] = true
		if _, err := api.database.SaveForSession(ctx, session, jwxkQueryNamespace, key, json.RawMessage("null"), 1, true); err != nil {
			t.Fatal(err)
		}
	})
	if !errors.Is(err, store.ErrConflict) {
		t.Fatal(err)
	}
	document, snapshot, err := api.jwxkQuerySnapshot(ctx, session, key)
	if err != nil || snapshot != nil || !document.Deleted {
		t.Fatal(document, snapshot, err)
	}
}
