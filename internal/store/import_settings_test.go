package store

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"testing"
)

func TestImportSettingsAtomicSecretCreation(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	database.AddAccount(ctx, "student")
	generation, _ := database.Generation(ctx, "student")
	session := Session{Account: "student", Generation: generation}
	secret := []byte(`{"smtp_password":"fixture-private"}`)
	changes := []DocumentChange{{Namespace: "settings", Key: "legacy-mail-import", Payload: json.RawMessage(`{"version":1}`)}}
	if _, err := database.ImportSettingsForSession(ctx, session, changes, secret); err != nil {
		t.Fatal(err)
	}
	var ciphertext []byte
	if err := database.database.QueryRowContext(ctx, "SELECT ciphertext FROM secrets WHERE account_id='student'").Scan(&ciphertext); err != nil || bytes.Contains(ciphertext, []byte("fixture-private")) {
		t.Fatal("unencrypted secret", err)
	}
	changes[0].Key = "another-document"
	if _, err := database.ImportSettingsForSession(ctx, session, changes, []byte(`{}`)); !errors.Is(err, ErrConflict) {
		t.Fatal("secret overwrite accepted", err)
	}
	if _, err := database.Read(ctx, "student", "settings", "another-document"); !errors.Is(err, ErrMissing) {
		t.Fatal("failed secret insert left document", err)
	}
	database.RevokeAccount(ctx, "student")
	if _, err := database.ImportSettingsForSession(ctx, session, changes, nil); !errors.Is(err, ErrAccess) {
		t.Fatal("revoked generation imported", err)
	}
}
