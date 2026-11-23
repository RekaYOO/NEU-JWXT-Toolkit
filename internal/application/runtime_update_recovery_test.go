package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUpdateJobRecoveryVerifiesCompletedFile(t *testing.T) {
	for _, scenario := range []string{"valid", "changed", "missing", "outside", "interrupted"} {
		t.Run(scenario, func(t *testing.T) {
			api, _ := testApplication(t, "local-single")
			directory := t.TempDir()
			payload := []byte("synthetic-release")
			digest := sha256.Sum256(payload)
			path := filepath.Join(directory, "release.zip")
			if scenario == "outside" {
				path = filepath.Join(t.TempDir(), "release.zip")
			}
			if err := os.WriteFile(path, payload, 0600); err != nil {
				t.Fatal(err)
			}
			job := runtimeUpdateJob{ID: strings.Repeat("a", 32), State: "downloaded", Total: int64(len(payload)), Downloaded: int64(len(payload)), SHA256: hex.EncodeToString(digest[:]), Path: path}
			if scenario == "interrupted" {
				job.State = "verifying"
			}
			if scenario == "changed" {
				if err := os.WriteFile(path, []byte("modified-release!"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "missing" {
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
			}
			encoded, _ := json.Marshal(job)
			if err := api.database.SaveUpdateJob(context.Background(), nil, encoded); err != nil {
				t.Fatal(err)
			}
			if err := api.ConfigureUpdateDirectory(directory); err != nil {
				t.Fatal(err)
			}
			recovered := api.releaseChecker.job
			if scenario == "valid" {
				if recovered.State != "downloaded" || recovered.Path != path {
					t.Fatal(recovered)
				}
			} else if recovered.State != "failed" || recovered.Path != "" || recovered.Error == "" {
				t.Fatal(recovered)
			}
			saved, err := api.database.SiteSetting(context.Background(), "runtime-update-job")
			var persisted runtimeUpdateJob
			if err != nil || json.Unmarshal(saved, &persisted) != nil || persisted.State != recovered.State {
				t.Fatal(string(saved), err)
			}
		})
	}
}

func TestPreparedLayoutRecovery(t *testing.T) {
	for _, scenario := range []string{"complete", "missing-page", "removed", "outside"} {
		t.Run(scenario, func(t *testing.T) {
			api, _ := testApplication(t, "local-single")
			directory := t.TempDir()
			id := strings.Repeat("b", 32)
			parent := filepath.Join(directory, "download-"+id)
			if err := os.Mkdir(parent, 0700); err != nil {
				t.Fatal(err)
			}
			archive := filepath.Join(parent, "release.zip")
			payload := []byte("verified fixture")
			if err := os.WriteFile(archive, payload, 0600); err != nil {
				t.Fatal(err)
			}
			prepared := filepath.Join(parent, ".toolbox-update-123")
			if scenario == "outside" {
				prepared = filepath.Join(t.TempDir(), ".toolbox-update-123")
			}
			if scenario != "removed" {
				if err := os.MkdirAll(filepath.Join(prepared, "frontend", "build"), 0700); err != nil {
					t.Fatal(err)
				}
				for _, name := range []string{"VERSION", "toolbox.exe", "frontend/build/index.html"} {
					if scenario == "missing-page" && name == "frontend/build/index.html" {
						continue
					}
					if err := os.WriteFile(filepath.Join(prepared, name), payload, 0600); err != nil {
						t.Fatal(err)
					}
				}
			}
			digest := sha256.Sum256(payload)
			job := runtimeUpdateJob{ID: id, State: "downloaded", Total: int64(len(payload)), SHA256: hex.EncodeToString(digest[:]), Path: archive, PreparedPath: prepared}
			encoded, err := json.Marshal(job)
			if err != nil {
				t.Fatal(err)
			}
			if err := api.database.SaveUpdateJob(context.Background(), nil, encoded); err != nil {
				t.Fatal(err)
			}
			if err := api.ConfigureUpdateDirectory(directory); err != nil {
				t.Fatal(err)
			}
			got := api.releaseChecker.job
			if got.State != "downloaded" || got.Path != archive || (got.PreparedPath != "") != (scenario == "complete") {
				t.Fatal(got)
			}
		})
	}
}

func TestUpdateJobPersistenceRejectsConcurrentWriter(t *testing.T) {
	api, _ := testApplication(t, "local-single")
	ctx := context.Background()
	first := []byte(`{"job_id":"first","state":"downloading"}`)
	second := []byte(`{"job_id":"second","state":"downloading"}`)
	if err := api.database.SaveUpdateJob(ctx, nil, first); err != nil {
		t.Fatal(err)
	}
	if err := api.database.SaveUpdateJob(ctx, nil, second); err == nil {
		t.Fatal("second initial writer replaced job")
	}
	if err := api.database.SaveUpdateJob(ctx, first, second); err != nil {
		t.Fatal(err)
	}
	if err := api.database.SaveUpdateJob(ctx, first, first); err == nil {
		t.Fatal("stale progress overwrote current job")
	}
}

func TestDesktopInstallRecoveryDoesNotReplay(t *testing.T) {
	for initial, expected := range map[string]string{"installing": "install_unknown", "install_unknown": "install_unknown", "selected": "selected"} {
		t.Run(initial, func(t *testing.T) {
			api, _ := testApplication(t, "local-single")
			api.SetDesktopUpdateInstaller(func(string, string) error { t.Fatal("installation replayed during recovery"); return nil })
			job := runtimeUpdateJob{ID: strings.Repeat("a", 32), State: initial}
			payload, err := json.Marshal(job)
			if err != nil {
				t.Fatal(err)
			}
			if err := api.database.SaveUpdateJob(context.Background(), nil, payload); err != nil {
				t.Fatal(err)
			}
			if err := api.ConfigureUpdateDirectory(t.TempDir()); err != nil {
				t.Fatal(err)
			}
			if api.releaseChecker.job.State != expected {
				t.Fatalf("got %s, want %s", api.releaseChecker.job.State, expected)
			}
		})
	}
}
