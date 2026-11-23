package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func recoveryFixture(t *testing.T) (*Store, Session, string) {
	t.Helper()
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { database.Close() })
	ctx := context.Background()
	if err := database.AddAccount(ctx, "student-a"); err != nil {
		t.Fatal(err)
	}
	session := Session{Account: "student-a", Generation: 1}
	if _, err := database.SaveForSession(ctx, session, "auth-recovery", "config", []byte(`{"public_base_url":"https://toolbox.example.invalid","link_ttl_hours":3,"epoch":"first"}`), 0, false); err != nil {
		t.Fatal(err)
	}
	recovery, err := database.CreateRecoveryContext(ctx, session, "primary", "grade-tracking", "auth-lost", "first", 3*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	return database, session, recovery.ID
}

func TestRecoveryContextFixedExpirySignatureAndSingleConsumption(t *testing.T) {
	database, session, id := recoveryFixture(t)
	ctx := context.Background()
	token, err := database.RecoveryToken(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	first, err := database.RecoveryContext(ctx, token)
	if err != nil {
		t.Fatal(err)
	}
	reused, err := database.CreateRecoveryContext(ctx, session, "primary", "grade-tracking", "auth-lost", "first", 168*time.Hour)
	if err != nil || first.ID != reused.ID || first.Expires != reused.Expires {
		t.Fatal("repeat creation renewed or replaced an active link", err)
	}
	for _, invalid := range []string{"", token + "x", strings.Repeat("a", 64) + token[64:], token[:65] + strings.Repeat("0", 64)} {
		if _, err := database.RecoveryContext(ctx, invalid); !errors.Is(err, ErrMissing) {
			t.Fatal("invalid signature accepted", err)
		}
	}
	var stored string
	if err := database.database.QueryRow(`SELECT token_hash FROM auth_recovery_contexts WHERE id=?`, id).Scan(&stored); err != nil || stored == token || stored != recoveryHash(token) {
		t.Fatal("token storage is not a hash", err)
	}
	var winners atomic.Int32
	var workers sync.WaitGroup
	for range 8 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			if database.FinishRecoveryContext(ctx, token, "consumed") == nil {
				winners.Add(1)
			}
		}()
	}
	workers.Wait()
	if winners.Load() != 1 {
		t.Fatal("context was not consumed exactly once", winners.Load())
	}
	if _, err := database.RecoveryToken(ctx, id); !errors.Is(err, ErrMissing) {
		t.Fatal("consumed token reconstructed", err)
	}
}

func TestRecoveryContextInvalidation(t *testing.T) {
	for _, scenario := range []string{"expiry", "logout", "disabled", "changed-site", "cancel"} {
		t.Run(scenario, func(t *testing.T) {
			database, session, id := recoveryFixture(t)
			ctx := context.Background()
			token, err := database.RecoveryToken(ctx, id)
			if err != nil {
				t.Fatal(err)
			}
			switch scenario {
			case "expiry":
				_, err = database.database.Exec(`UPDATE auth_recovery_contexts SET expires_at=? WHERE id=?`, time.Now().Unix()-1, id)
			case "logout":
				err = database.RevokeAccount(ctx, session.Account)
			case "disabled":
				err = database.SetEnabled(ctx, session.Account, false)
			case "changed-site":
				_, err = database.SaveForSession(ctx, session, "auth-recovery", "config", []byte(`{"public_base_url":"https://other.example.invalid","epoch":"second"}`), 1, false)
			case "cancel":
				err = database.FinishRecoveryContext(ctx, token, "cancelled")
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := database.RecoveryContext(ctx, token); !errors.Is(err, ErrMissing) {
				t.Fatal("invalidated link remained usable", err)
			}
		})
	}
}

func TestRecoveryContextRestartAndConfigurationBinding(t *testing.T) {
	ctx := context.Background()
	directory := t.TempDir()
	database, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	database.AddAccount(ctx, "student-a")
	session := Session{Account: "student-a", Generation: 1}
	payload, _ := json.Marshal(map[string]any{"public_base_url": "https://toolbox.example.invalid", "epoch": "epoch"})
	if _, err := database.SaveForSession(ctx, session, "auth-recovery", "config", payload, 0, false); err != nil {
		t.Fatal(err)
	}
	first, err := database.CreateRecoveryContext(ctx, session, "jwxk", "selection", "task", "epoch", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	token, err := database.RecoveryToken(ctx, first.ID)
	if err != nil {
		t.Fatal(err)
	}
	database.Close()
	database, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	reopened, err := database.RecoveryContext(ctx, token)
	if err != nil || reopened.Expires != first.Expires || reopened.Target != "jwxk" || reopened.Account != session.Account {
		t.Fatal("restart lost or renewed context", err)
	}
	if _, err := database.CreateRecoveryContext(ctx, session, "primary", "tracking", "task", "stale-epoch", time.Hour); !errors.Is(err, ErrAccess) {
		t.Fatal("stale configuration could issue links", err)
	}
	if _, err := database.CreateRecoveryContext(ctx, Session{Account: "other", Generation: 1}, "primary", "tracking", "task", "epoch", time.Hour); !errors.Is(err, ErrAccess) {
		t.Fatal("another account could issue links", err)
	}
}

func TestRecoveryContextsRetainActiveLinksWithinBound(t *testing.T) {
	database, session, original := recoveryFixture(t)
	ctx := context.Background()
	for index := 1; index < 64; index++ {
		if _, err := database.CreateRecoveryContext(ctx, session, "jwxk", "selection", fmt.Sprint(index), "first", time.Hour); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := database.CreateRecoveryContext(ctx, session, "jwxk", "selection", "overflow", "first", time.Hour); !errors.Is(err, ErrConflict) {
		t.Fatal("active context limit ignored", err)
	}
	token, err := database.RecoveryToken(ctx, original)
	if err != nil {
		t.Fatal("active context discarded by capacity cleanup", err)
	}
	if err := database.FinishRecoveryContext(ctx, token, "cancelled"); err != nil {
		t.Fatal(err)
	}
	if _, err := database.CreateRecoveryContext(ctx, session, "jwxk", "selection", "replacement", "first", time.Hour); err != nil {
		t.Fatal("terminal context did not free capacity", err)
	}
}
