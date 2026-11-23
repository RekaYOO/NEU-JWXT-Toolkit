package store

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

func TestAccountIsolationConflictAndDeletion(t *testing.T) {
	directory := t.TempDir()
	instance, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := instance.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	payload := json.RawMessage(`{"name":"private plan"}`)
	if _, err := instance.Save(ctx, "student-a", "gpa", "plan", payload, 0, false); err != nil {
		t.Fatal(err)
	}
	if _, err := instance.Read(ctx, "student-b", "gpa", "plan"); !errors.Is(err, ErrMissing) {
		t.Fatalf("cross-account read: %v", err)
	}
	if _, err := instance.Save(ctx, "student-b", "gpa", "plan", payload, 1, false); !errors.Is(err, ErrConflict) {
		t.Fatalf("cross-account overwrite: %v", err)
	}
	if _, err := instance.Save(ctx, "student-a", "gpa", "plan", payload, 1, true); err != nil {
		t.Fatal(err)
	}
	if err := instance.Close(); err != nil {
		t.Fatal(err)
	}
	instance, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer instance.Close()
	for _, revision := range []int64{0, 1} {
		if _, err := instance.Save(ctx, "student-a", "gpa", "plan", payload, revision, false); !errors.Is(err, ErrConflict) {
			t.Fatalf("stale draft resurrected: %v", err)
		}
	}
	document, err := instance.Read(ctx, "student-a", "gpa", "plan")
	if err != nil || !document.Deleted || document.Revision != 2 || string(document.Payload) != "null" {
		t.Fatalf("deleted document: %+v %v", document, err)
	}
	if err := instance.SetEnabled(ctx, "student-a", false); err != nil {
		t.Fatal(err)
	}
	if _, err := instance.Read(ctx, "student-a", "gpa", "plan"); !errors.Is(err, ErrAccess) {
		t.Fatalf("disabled read: %v", err)
	}
	if _, err := instance.Save(ctx, "student-a", "gpa", "plan", payload, 2, false); !errors.Is(err, ErrAccess) {
		t.Fatalf("disabled write: %v", err)
	}
	if err := instance.SetEnabled(ctx, "student-a", true); err != nil {
		t.Fatal(err)
	}
	if generation, err := instance.Generation(ctx, "student-a"); err != nil || generation != 3 {
		t.Fatalf("revocation generation: %d %v", generation, err)
	}
}
