package store

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestEvaluationAttemptSurvivesRestartAndIsAccountBound(t *testing.T) {
	directory := t.TempDir()
	database, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	session := Session{Account: "student-a", Generation: 1}
	key := strings.Repeat("a", 64)
	attempt, err := database.BeginEvaluation(ctx, session, key)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.BeginEvaluation(ctx, session, key); !errors.Is(err, ErrConflict) {
		t.Fatal("duplicate dispatch permitted")
	}
	database.Close()
	database, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if _, err := database.BeginEvaluation(ctx, session, key); !errors.Is(err, ErrConflict) {
		t.Fatal("restart forgot uncertain operation")
	}
	if _, err := database.BeginEvaluation(ctx, Session{Account: "student-b", Generation: 1}, key); err != nil {
		t.Fatal("another account affected")
	}
	if err := database.FinishEvaluation(ctx, session, key, attempt.Attempt, "rejected"); err != nil {
		t.Fatal(err)
	}
	next, err := database.BeginEvaluation(ctx, session, key)
	if err != nil || next.Attempt != 2 {
		t.Fatal(next, err)
	}
	if err := database.FinishEvaluation(ctx, session, key, attempt.Attempt, "confirmed"); !errors.Is(err, ErrConflict) {
		t.Fatal("old response replaced a newer attempt")
	}
	if err := database.RevokeAccount(ctx, session.Account); err != nil {
		t.Fatal(err)
	}
	if err := database.FinishEvaluation(ctx, session, key, next.Attempt, "confirmed"); !errors.Is(err, ErrConflict) {
		t.Fatal("revoked session changed result")
	}
	if _, err := database.BeginEvaluation(ctx, session, strings.Repeat("b", 64)); !errors.Is(err, ErrAccess) {
		t.Fatal("revoked session began submit")
	}
}
