package store

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestMailSecretGenerationAndCiphertext(t *testing.T) {
	directory := t.TempDir()
	instance, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer instance.Close()
	ctx := context.Background()
	if err := instance.AddAccount(ctx, "account-a"); err != nil {
		t.Fatal(err)
	}
	generation, _ := instance.Generation(ctx, "account-a")
	session := Session{Account: "account-a", Generation: generation}
	secret := []byte(`{"smtp_password":"mail-private-test-only"}`)
	if err := instance.SaveSecretForSession(ctx, session, "mail-config", secret); err != nil {
		t.Fatal(err)
	}
	var ciphertext []byte
	if err := instance.database.QueryRowContext(ctx, "SELECT ciphertext FROM secrets WHERE purpose='mail-config'").Scan(&ciphertext); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(ciphertext, []byte("mail-private-test-only")) {
		t.Fatal("mail password stored in plaintext")
	}
	if err := instance.RevokeAccount(ctx, session.Account); err != nil {
		t.Fatal(err)
	}
	if err := instance.SaveSecretForSession(ctx, session, "mail-config", []byte(`{}`)); !errors.Is(err, ErrAccess) {
		t.Fatal("revoked identity changed mail credentials", err)
	}
	payload, err := instance.ReadSecret(ctx, session.Account, "mail-config")
	if err != nil || !bytes.Equal(payload, secret) {
		t.Fatal("original credentials lost", err)
	}
}

func TestSecretsAndSessionRevocation(t *testing.T) {
	directory := t.TempDir()
	instance, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, account := range []string{"account-a", "account-b"} {
		if err := instance.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	secret := []byte("test-only-private-cookie-and-password")
	if err := instance.SaveSecret(ctx, "account-a", "campus-session", secret); err != nil {
		t.Fatal(err)
	}
	if _, err := instance.ReadSecret(ctx, "account-b", "campus-session"); err == nil {
		t.Fatal("cross-account secret exposed")
	}
	token, err := instance.CreateSession(ctx, "account-a", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := instance.Session(ctx, token); err != nil {
		t.Fatal(err)
	}
	if err := instance.RevokeAccount(ctx, "account-a"); err != nil {
		t.Fatal(err)
	}
	if _, err := instance.Session(ctx, token); err == nil {
		t.Fatal("revoked session remains valid")
	}
	if err := instance.Close(); err != nil {
		t.Fatal(err)
	}
	databaseBytes, err := os.ReadFile(filepath.Join(directory, "toolbox.db"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(databaseBytes, secret) || bytes.Contains(databaseBytes, []byte(token)) {
		t.Fatal("database contains plaintext credentials or session token")
	}
	instance, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer instance.Close()
	restored, err := instance.ReadSecret(ctx, "account-a", "campus-session")
	if err != nil || !bytes.Equal(secret, restored) {
		t.Fatal("encrypted secret did not survive restart")
	}
	if _, err := instance.database.Exec(`UPDATE secrets SET account_id='account-b' WHERE account_id='account-a'`); err != nil {
		t.Fatal(err)
	}
	if _, err := instance.ReadSecret(ctx, "account-b", "campus-session"); err == nil {
		t.Fatal("ciphertext accepted for a different owner")
	}
}
