package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestUpgradeStartupGate(t *testing.T) {
	var ready atomic.Bool
	calls := 0
	handler := upgradeStartupGate(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		calls++
		response.WriteHeader(http.StatusNoContent)
	}), &ready)
	for _, scenario := range []struct {
		method, path  string
		ready         bool
		status, calls int
	}{
		{"POST", "/api/health", false, 503, 0}, {"POST", "/api/settings", false, 503, 0}, {"GET", "/", false, 503, 0},
		{"GET", "/api/health", false, 204, 1}, {"POST", "/api/settings", true, 204, 2},
	} {
		ready.Store(scenario.ready)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(scenario.method, scenario.path, nil))
		if response.Code != scenario.status || calls != scenario.calls {
			t.Fatal(scenario, response.Code, calls)
		}
	}
}

func TestUpgradeCheckpointSurvivesRetry(t *testing.T) {
	root := t.TempDir()
	stage := ".toolbox-update-123"
	for name, content := range map[string]string{"toolbox.exe": "fixture", "VERSION": "baseline", "frontend/build/index.html": "fixture", activeReleaseFile: stage, stage + "/toolbox.exe": "candidate"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	data := t.TempDir()
	database, err := store.Open(data)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	if err := database.AddAccount(ctx, "before"); err != nil {
		t.Fatal(err)
	}
	exe := filepath.Join(root, stage, "toolbox.exe")
	if err := registerUpgradeCheckpoint(ctx, data, exe); err != nil {
		t.Fatal(err)
	}
	if err := database.AddAccount(ctx, "after"); err != nil {
		t.Fatal(err)
	}
	if err := registerUpgradeCheckpoint(ctx, data, exe); err != nil {
		t.Fatal(err)
	}
	snapshots, err := filepath.Glob(filepath.Join(data, ".toolbox-upgrade-snapshot-*"))
	if err != nil || len(snapshots) != 1 {
		t.Fatal(snapshots, err)
	}
	saved, err := store.Open(snapshots[0])
	if err != nil {
		t.Fatal(err)
	}
	defer saved.Close()
	if _, err := saved.Generation(ctx, "before"); err != nil {
		t.Fatal(err)
	}
	if _, err := saved.Generation(ctx, "after"); err == nil {
		t.Fatal("retry replaced pre-upgrade snapshot")
	}
	if err := saved.Close(); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	if err := restoreUpgradeCheckpoint(ctx, data, exe); err != nil {
		t.Fatal(err)
	}
	restored, err := store.Open(data)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := restored.Generation(ctx, "before"); err != nil {
		t.Fatal(err)
	}
	if _, err := restored.Generation(ctx, "after"); err == nil {
		t.Fatal("failed-version write survived rollback")
	}
	if err := restored.AddAccount(ctx, "after-rollback"); err != nil {
		t.Fatal(err)
	}
	if err := restored.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(snapshots[0]); !os.IsNotExist(err) {
		t.Fatal("consumed snapshot still present", err)
	}
	if err := registerUpgradeCheckpoint(ctx, data, exe); err != nil {
		t.Fatal(err)
	}
	snapshots, err = filepath.Glob(filepath.Join(data, ".toolbox-upgrade-snapshot-*"))
	if err != nil || len(snapshots) != 1 {
		t.Fatal(snapshots, err)
	}
	fresh, err := store.Open(snapshots[0])
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fresh.Generation(ctx, "after-rollback"); err != nil {
		t.Fatal("retry lost changes since rollback", err)
	}
	if err := fresh.Close(); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"backend":"go","version":"candidate"}`))
	}))
	defer server.Close()
	if err := confirmUpgradeCheckpoint(data, exe, server.URL, "other", func() error { t.Fatal("restored before health check"); return nil }); err == nil {
		t.Fatal("accepted wrong version")
	}
	if _, err := os.Stat(filepath.Join(snapshots[0], "vault.key")); err != nil {
		t.Fatal(err)
	}
	if err := confirmUpgradeCheckpoint(data, exe, server.URL, "candidate", func() error { return os.ErrInvalid }); err == nil {
		t.Fatal("accepted failed session restore")
	}
	if _, err := os.Stat(filepath.Join(snapshots[0], "vault.key")); err != nil {
		t.Fatal(err)
	}
	if err := confirmUpgradeCheckpoint(data, exe, server.URL, "candidate", func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	if err := registerUpgradeCheckpoint(ctx, data, exe); err != nil {
		t.Fatal(err)
	}
	remaining, err := filepath.Glob(filepath.Join(data, ".toolbox-upgrade-snapshot-*"))
	if err != nil || len(remaining) != 0 {
		t.Fatal(remaining, err)
	}
}
