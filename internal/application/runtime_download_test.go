package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestRuntimeDownloadVerifiesBeforePublishing(t *testing.T) {
	for _, kind := range []string{"valid", "stage-failed", "wrong-hash", "truncated", "oversized", "http-error"} {
		t.Run(kind, func(t *testing.T) {
			api, _ := testApplication(t, "local-single")
			payload := "synthetic-release-archive"
			hash := sha256.Sum256([]byte(payload))
			asset := runtimeReleaseAsset{Name: "release.zip", Size: int64(len(payload)), SHA256: hex.EncodeToString(hash[:]), URL: "https://github.com/" + releaseRepository + "/releases/download/v9.0.0/release.zip"}
			if kind == "wrong-hash" {
				asset.SHA256 = strings.Repeat("0", 64)
			}
			if kind == "truncated" {
				payload = payload[:5]
			}
			if kind == "oversized" {
				payload += "extra"
			}
			client := &http.Client{Transport: releaseTransport(func(request *http.Request) (*http.Response, error) {
				status := 200
				if kind == "http-error" {
					status = 503
				}
				return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(payload)), ContentLength: -1}, nil
			})}
			api.releaseChecker.job = &runtimeUpdateJob{ID: "fixture", State: "downloading"}
			directory := t.TempDir()
			calls := 0
			api.SetUpdatePreparer(func(archive, digest, parent string) (string, error) {
				calls++
				if digest != asset.SHA256 || filepath.Dir(archive) != parent {
					t.Fatal("invalid staging inputs")
				}
				if kind == "stage-failed" {
					return "", os.ErrPermission
				}
				return filepath.Join(parent, "prepared"), nil
			})
			api.downloadRelease(context.Background(), client, asset, "fixture", directory)
			job := api.releaseChecker.job
			if kind == "valid" || kind == "stage-failed" {
				if calls != 1 || (job.PreparedPath != "") != (kind == "valid") {
					t.Fatal("verified package was not prepared")
				}
				actual, err := os.ReadFile(job.Path)
				if kind == "stage-failed" && !strings.Contains(job.Message, "暂存失败") {
					t.Fatal(job.Message)
				}
				if job.State != "downloaded" || err != nil || string(actual) != payload {
					t.Fatal(job, err)
				}
				if len(job.History) != 2 || job.History[0].State != "verifying" || job.History[1].State != "downloaded" || job.Updated == "" {
					t.Fatal("missing transition history", job)
				}
			} else {
				if calls != 0 {
					t.Fatal("unexpected staging before successful verification")
				}
				if job.State != "failed" || job.Path != "" {
					t.Fatal(job)
				}
				filepath.WalkDir(directory, func(path string, entry os.DirEntry, err error) error {
					if err != nil {
						t.Fatal(err)
					}
					if !entry.IsDir() {
						t.Fatal("failed payload remained", path)
					}
					return nil
				})
			}
		})
	}
}

func TestInterruptedDownloadCleanupPreservesArchive(t *testing.T) {
	directory := t.TempDir()
	id := strings.Repeat("a", 32)
	stage := filepath.Join(directory, "download-"+id)
	if err := os.Mkdir(stage, 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"payload.partial", "release.zip"} {
		if err := os.WriteFile(filepath.Join(stage, name), []byte(name), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for attempt := 0; attempt < 2; attempt++ {
		if err := cleanInterruptedDownload(directory, id); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := os.Stat(filepath.Join(stage, "payload.partial")); !os.IsNotExist(err) {
		t.Fatal("unexpected partial status", err)
	}
	data, err := os.ReadFile(filepath.Join(stage, "release.zip"))
	if err != nil || string(data) != "release.zip" {
		t.Fatal("archive changed", err)
	}
}

func TestRemoteDownloadRequiresAdmin(t *testing.T) {
	_, handler := testApplication(t, "remote-multi")
	for _, route := range []struct{ method, path string }{{"POST", "/api/runtime/update/download"}, {"GET", "/api/runtime/update/fixture"}} {
		response := apiRequest(handler, route.method, route.path, "", nil)
		if response.Code != 403 && response.Code != 401 {
			t.Fatal(response.Code, response.Body.String())
		}
	}
}

func TestRuntimeDownloadRoutesPublishProgress(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	api.version = "2.2.4"
	api.ConfigureUpdateDirectory(t.TempDir())
	payload := "synthetic-download"
	hash := sha256.Sum256([]byte(payload))
	asset := runtimeReleaseAsset{Name: "release.zip", Size: int64(len(payload)), SHA256: hex.EncodeToString(hash[:]), URL: "https://github.com/" + releaseRepository + "/releases/download/v9.0.0/release.zip"}
	api.releaseChecker.latest = &runtimeRelease{Version: "9.0.0", Assets: map[string]runtimeReleaseAsset{runtime.GOOS + "-" + runtime.GOARCH: asset}}
	api.releaseChecker.checked = time.Now()
	api.releaseChecker.client = &http.Client{Transport: releaseTransport(func(request *http.Request) (*http.Response, error) {
		if request.URL.String() != asset.URL {
			t.Error("download accepted caller URL")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(payload)), ContentLength: int64(len(payload))}, nil
	})}
	response := apiRequest(handler, "POST", "/api/runtime/update/download", `{"url":"https://evil.invalid/file"}`, nil)
	var job runtimeUpdateJob
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &job) != nil || job.ID == "" {
		t.Fatal(response.Code, response.Body.String())
	}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		response = apiRequest(handler, "GET", "/api/runtime/update/"+job.ID, "", nil)
		if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &job) != nil {
			t.Fatal(response)
		}
		if job.State == "downloaded" {
			return
		}
		if job.State == "failed" {
			t.Fatal(job)
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("download did not finish", job)
}
