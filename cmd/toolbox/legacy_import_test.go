package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/application"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/processlock"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestLegacyCommandPreviewAndApply(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	data := filepath.Join(root, "go")
	database, err := store.Open(data)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.AddAccount(ctx, "student"); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(root, "legacy")
	if err := os.MkdirAll(filepath.Join(source, "成绩"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "成绩", "plan.json"), []byte(`{"courses":[],"export_info":{"exported_by":"student"}}`), 0600); err != nil {
		t.Fatal(err)
	}
	output := &bytes.Buffer{}
	args := []string{"--data", data, "--source", source, "--account", "student"}
	lock, err := processlock.Acquire(data)
	if err != nil {
		t.Fatal(err)
	}
	importErr := runLegacyImport(ctx, args, output)
	if err := lock.Close(); err != nil {
		t.Fatal(err)
	}
	if !errors.Is(importErr, processlock.ErrInUse) || output.Len() != 0 {
		t.Fatalf("import against a running instance: %v, %s", importErr, output)
	}
	if err := runLegacyImport(ctx, args, output); err != nil {
		t.Fatal(err)
	}
	var preview application.LegacyImportReport
	if err := json.Unmarshal(output.Bytes(), &preview); err != nil || len(preview.Items) != 1 || preview.Applied != 0 {
		t.Fatal(output.String(), err)
	}
	output.Reset()
	if err := runLegacyImport(ctx, append(args, "--apply-digest", preview.Digest), output); err != nil {
		t.Fatal(err)
	}
	var applied application.LegacyImportReport
	if err := json.Unmarshal(output.Bytes(), &applied); err != nil || applied.Applied != 1 {
		t.Fatal(output.String(), err)
	}
	if err := runLegacyImport(ctx, nil, output); err == nil {
		t.Fatal("implicit account or paths accepted")
	}
	if err := os.WriteFile(filepath.Join(source, "grade_tracking_config.json"), []byte(`{"enabled":true,"interval_minutes":60}`), 0600); err != nil {
		t.Fatal(err)
	}
	output.Reset()
	sharedArgs := append(append([]string{}, args...), "--shared-settings-owner", "student")
	if err := runLegacyImport(ctx, sharedArgs, output); err != nil {
		t.Fatal(err)
	}
	var shared application.LegacyImportReport
	if err := json.Unmarshal(output.Bytes(), &shared); err != nil || len(shared.Items) != 1 || shared.Items[0].Namespace != "grade-tracking" {
		t.Fatal(output.String(), err)
	}
	output.Reset()
	if err := runLegacyImport(ctx, append(sharedArgs, "--apply-digest", shared.Digest), output); err != nil {
		t.Fatal(err)
	}
	if err := runLegacyImport(ctx, append(sharedArgs, "--terms", "2026-2027-1"), output); err == nil {
		t.Fatal("mixed import scope accepted")
	}
}
