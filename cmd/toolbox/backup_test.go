package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestDataCommandsUseExplicitPathsAndRoundTrip(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	output := &bytes.Buffer{}
	t.Setenv("NEU_JWXT_BACKUP_PASSWORD", "synthetic-command-password")
	if err := runDataCommand(ctx, "backup", nil, output); err == nil {
		t.Fatal("implicit paths accepted")
	}
	if err := runDataCommand(ctx, "backup", []string{"--data", filepath.Join(root, "missing"), "--file", filepath.Join(root, "unused.age")}, output); err == nil {
		t.Fatal("missing source created")
	}
	source := filepath.Join(root, "source")
	database, err := store.Open(source)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	archive := filepath.Join(root, "backup.age")
	if err := runDataCommand(ctx, "backup", []string{"--data", source, "--file", archive}, output); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "restored")
	if err := runDataCommand(ctx, "restore", []string{"--data", target, "--file", archive}, output); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(target, "vault.key")); err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(output.Bytes(), []byte("Restore completed")) {
		t.Fatal(output.String())
	}
	t.Setenv("NEU_JWXT_BACKUP_PASSWORD", "")
	if err := runDataCommand(ctx, "backup", []string{"--data", source, "--file", filepath.Join(root, "no-password.age")}, output); err == nil {
		t.Fatal("missing password accepted")
	}
}
