package store

import (
	"context"
	"errors"
	"testing"
)

func TestLateDiagnosticAfterClear(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	session := Session{Account: "fixture", Generation: 1}
	entry := DiagnosticEntry{Category: "sync", Summary: "before"}
	if err := database.AddAccount(ctx, session.Account); err != nil {
		t.Fatal(err)
	}
	if err := database.AppendSessionDiagnostic(ctx, session, entry); err != nil {
		t.Fatal(err)
	}
	if err := database.RevokeAccountSession(ctx, session, true); err != nil {
		t.Fatal(err)
	}
	if err := database.AppendSessionDiagnostic(ctx, session, entry); !errors.Is(err, ErrAccess) {
		t.Fatal("late write", err)
	}
	session.Generation++
	entry.Summary = "after"
	if err := database.AppendSessionDiagnostic(ctx, session, entry); err != nil {
		t.Fatal(err)
	}
	rows, err := database.DiagnosticEntries(ctx, DiagnosticQuery{Account: session.Account, Limit: 10})
	if err != nil || len(rows) != 1 || rows[0].Summary != "after" {
		t.Fatal(rows, err)
	}
}

func TestDiagnosticCleanupRetainsOtherAccountsAndBoundaryDay(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		for _, day := range []string{"2026-01-01", "2026-01-02"} {
			if _, err := database.database.Exec(`INSERT INTO diagnostic_logs(account_id,category,day,timestamp,payload) VALUES(?,'system',?,?,'{}')`, account, day, day); err != nil {
				t.Fatal(err)
			}
		}
	}
	deleted, err := database.CleanupDiagnostics(ctx, "student-a", false, "2026-01-02")
	if err != nil || deleted != 1 {
		t.Fatal(deleted, err)
	}
	for account, want := range map[string]int{"student-a": 1, "student-b": 2} {
		files, err := database.DiagnosticFiles(ctx, DiagnosticQuery{Account: account})
		if err != nil || len(files) != want || files[0].Date != "2026-01-02" {
			t.Fatal(account, files, err)
		}
	}
}
