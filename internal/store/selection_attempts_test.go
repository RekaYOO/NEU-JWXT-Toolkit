package store

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestSelectionJournalSurvivesRestartAndBlocksUnknownReplay(t *testing.T) {
	directory := t.TempDir()
	database, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := database.AddAccount(ctx, "student"); err != nil {
		t.Fatal(err)
	}
	generation, _ := database.Generation(ctx, "student")
	session := Session{Account: "student", Generation: generation}
	key := strings.Repeat("a", 64)
	attempt, err := database.BeginSelection(ctx, session, key)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.FinishSelection(ctx, session, key, attempt, "unknown"); err != nil {
		t.Fatal(err)
	}
	database.Close()
	database, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	previous, err := database.BeginSelection(ctx, session, key)
	if !errors.Is(err, ErrConflict) || previous.State != "unknown" {
		t.Fatal(previous, err)
	}
	if err := database.FinishSelection(ctx, session, key, attempt, "rejected"); !errors.Is(err, ErrConflict) {
		t.Fatal("old response overwrote current state", err)
	}
	if err := database.FinishSelection(ctx, session, key, previous, "confirmed"); err != nil {
		t.Fatal(err)
	}
	if _, err := database.BeginSelection(ctx, session, key); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	other := strings.Repeat("b", 64)
	attempt, err = database.BeginSelection(ctx, session, other)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.FinishSelection(ctx, session, other, attempt, "not_sent"); err != nil {
		t.Fatal(err)
	}
	if attempt, err = database.BeginSelection(ctx, session, other); err != nil || attempt.Number != 2 {
		t.Fatal(attempt, err)
	}
}
