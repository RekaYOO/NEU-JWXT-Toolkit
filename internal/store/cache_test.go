package store

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

func TestCacheFailureRevisionInvalidationAndRevocation(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	generation, err := database.Generation(ctx, "student-a")
	if err != nil {
		t.Fatal(err)
	}
	session := Session{Account: "student-a", Generation: generation}
	report := CacheEntry{Resource: "academic-report", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"credits":12}`), Revision: "report-1"}
	scores := CacheEntry{Resource: "scores", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"reference":"old"}`), Revision: "scores-1"}
	for _, entry := range []CacheEntry{report, scores} {
		if changed, err := database.CommitCache(ctx, session, entry, "test", nil); err != nil || !changed {
			t.Fatalf("initial commit: %v %v", changed, err)
		}
	}
	previous, err := database.ReadCache(ctx, session, "scores", "default")
	if err != nil {
		t.Fatal(err)
	}
	if err := database.CacheFailure(ctx, session, "scores", "default", "timeout"); err != nil {
		t.Fatal(err)
	}
	failed, err := database.ReadCache(ctx, session, "scores", "default")
	if err != nil || string(failed.Payload) != string(previous.Payload) || failed.Revision != previous.Revision || failed.ErrorKind != "timeout" {
		t.Fatalf("failed refresh lost cache: %+v %v", failed, err)
	}
	scores.Payload = json.RawMessage(`{"reference":"rotated"}`)
	if changed, err := database.CommitCache(ctx, session, scores, "test", []string{"academic-report"}); err != nil || changed {
		t.Fatalf("semantic no-change: %v %v", changed, err)
	}
	current, err := database.ReadCache(ctx, session, "scores", "default")
	if err != nil || current.SavedAt != previous.SavedAt || current.ErrorKind != "" || string(current.Payload) != string(scores.Payload) {
		t.Fatalf("reference refresh: %+v %v", current, err)
	}
	dependent, err := database.ReadCache(ctx, session, "academic-report", "default")
	if err != nil || dependent.Stale(time.Hour) {
		t.Fatalf("unchanged revision invalidated dependency: %+v %v", dependent, err)
	}
	scores.Revision = "scores-2"
	scores.Changes = map[string]any{"counts": map[string]int{"changed": 1}}
	if _, err := database.CommitCache(ctx, session, scores, "test", []string{"academic-report"}); err != nil {
		t.Fatal(err)
	}
	dependent, err = database.ReadCache(ctx, session, "academic-report", "default")
	if err != nil || !dependent.Invalidated {
		t.Fatalf("missing dependency invalidation: %+v %v", dependent, err)
	}
	zero := int64(0)
	events, _, err := database.CacheEvents(ctx, session, &zero, 100)
	if err != nil || len(events) != 4 || events[2].Changed || !events[3].Changed {
		t.Fatalf("events: %+v %v", events, err)
	}
	otherGeneration, _ := database.Generation(ctx, "student-b")
	if events[3].Changes["counts"].(map[string]any)["changed"] != float64(1) {
		t.Fatalf("event diff was not persisted: %+v", events[3])
	}
	other := Session{Account: "student-b", Generation: otherGeneration}
	events, _, err = database.CacheEvents(ctx, other, &zero, 100)
	if err != nil || len(events) != 0 {
		t.Fatalf("cross-account events: %+v %v", events, err)
	}
	if _, err := database.ReadCache(ctx, other, "scores", "default"); !errors.Is(err, ErrMissing) {
		t.Fatalf("cross-account cache: %v", err)
	}
	if err := database.SetEnabled(ctx, session.Account, false); err != nil {
		t.Fatal(err)
	}
	if _, err := database.CommitCache(ctx, session, scores, "late", nil); !errors.Is(err, ErrAccess) {
		t.Fatalf("revoked commit: %v", err)
	}
}
