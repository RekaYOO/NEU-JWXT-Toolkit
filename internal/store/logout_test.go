package store

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestAdmissionRestorationDoesNotReviveOldSession(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	login := func(account string) string {
		t.Helper()
		if err := database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
		token, err := database.CreateSession(ctx, account, time.Hour)
		if err != nil {
			t.Fatal(err)
		}
		return token
	}
	old, unaffected := login("student-a"), login("student-b")
	for _, enabled := range []bool{false, true} {
		if err := database.SetAdmission(ctx, "student-a", enabled, false); err != nil {
			t.Fatal(err)
		}
		if _, err := database.Session(ctx, old); err == nil {
			t.Fatalf("old session valid with admission=%t", enabled)
		}
		if session, err := database.Session(ctx, unaffected); err != nil || session.Account != "student-b" {
			t.Fatalf("unrelated session changed: %v", err)
		}
	}
	fresh, err := database.CreateSession(ctx, "student-a", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if session, err := database.Session(ctx, fresh); err != nil || session.Account != "student-a" {
		t.Fatalf("fresh session rejected: %v", err)
	}
}

func TestLogoutForgetsSchoolCredentialsAtomically(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	const account = "fixture"
	if err := database.AddAccount(ctx, account); err != nil {
		t.Fatal(err)
	}
	token, err := database.CreateSession(ctx, account, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	session, err := database.Session(ctx, token)
	if err != nil {
		t.Fatal(err)
	}
	purposes := []string{"campus-session", "campus-service-jwxk", "mail-config"}
	for _, purpose := range purposes {
		if err := database.SaveSecretForSession(ctx, session, purpose, []byte("fixture")); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := database.database.Exec(`CREATE TRIGGER fail_logout BEFORE DELETE ON secrets WHEN OLD.purpose='campus-service-jwxk' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END`); err != nil {
		t.Fatal(err)
	}
	if err := database.EndAccountSession(ctx, session, false, true); err == nil {
		t.Fatal("expected logout failure")
	}
	if _, err := database.Session(ctx, token); err != nil {
		t.Fatal("session revoked despite rollback", err)
	}
	for _, purpose := range purposes {
		if data, err := database.ReadSecret(ctx, account, purpose); err != nil || string(data) != "fixture" {
			t.Fatal("secret not preserved", purpose, err)
		}
	}
	if _, err := database.database.Exec(`DROP TRIGGER fail_logout`); err != nil {
		t.Fatal(err)
	}
	if err := database.EndAccountSession(ctx, session, false, true); err != nil {
		t.Fatal(err)
	}
	if _, err := database.Session(ctx, token); err == nil {
		t.Fatal("session survived logout")
	}
	for _, purpose := range purposes[:2] {
		if _, err := database.ReadSecret(ctx, account, purpose); !errors.Is(err, ErrMissing) {
			t.Fatal("credential survived logout", purpose, err)
		}
	}
	if data, err := database.ReadSecret(ctx, account, "mail-config"); err != nil || string(data) != "fixture" {
		t.Fatal("mail configuration changed", err)
	}
}
