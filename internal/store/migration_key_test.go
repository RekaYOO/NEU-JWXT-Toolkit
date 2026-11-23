package store

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
)

func TestUpgradeCanRetryWithMatchingKey(t *testing.T) {
	directory := t.TempDir()
	instance, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := instance.AddAccount(ctx, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err := instance.SaveSecret(ctx, "fixture", "mail-config", []byte("synthetic")); err != nil {
		t.Fatal(err)
	}
	if _, err := instance.database.Exec("PRAGMA user_version=10"); err != nil {
		t.Fatal(err)
	}
	if err := instance.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "vault.key")
	key, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	other := append([]byte(nil), key...)
	other[0] ^= 1
	if err := os.WriteFile(path, other, 0600); err != nil {
		t.Fatal(err)
	}
	if unexpected, err := Open(directory); err == nil {
		unexpected.Close()
		t.Fatal("mismatched key accepted")
	}
	database, err := sql.Open("sqlite", filepath.Join(directory, "toolbox.db"))
	if err != nil {
		t.Fatal(err)
	}
	var version int
	err = database.QueryRow("PRAGMA user_version").Scan(&version)
	database.Close()
	if err != nil || version != 10 {
		t.Fatal(version, err)
	}
	if err := os.WriteFile(path, key, 0600); err != nil {
		t.Fatal(err)
	}
	restored, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.Close()
	value, err := restored.ReadSecret(ctx, "fixture", "mail-config")
	if err != nil || string(value) != "synthetic" {
		t.Fatal("failed to recover original fixture", err)
	}
}

func TestInvalidKeyRollsBackSchemaUpgrade(t *testing.T) {
	directory := t.TempDir()
	database, err := sql.Open("sqlite", filepath.Join(directory, "toolbox.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if _, err := database.Exec("PRAGMA user_version=10"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "vault.key"), []byte("incomplete"), 0600); err != nil {
		t.Fatal(err)
	}
	if opened, err := Open(directory); err == nil {
		opened.Close()
		t.Fatal("invalid key accepted")
	}
	var version, tables int
	if err := database.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		t.Fatal(err)
	}
	if err := database.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE name='diagnostic_logs'").Scan(&tables); err != nil {
		t.Fatal(err)
	}
	if version != 10 || tables != 0 {
		t.Fatalf("failed upgrade changed database: version=%d tables=%d", version, tables)
	}
}
