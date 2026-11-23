package application

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestDesktopShutdownRequiresProcessTokenAndExactOrigin(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	var stopped atomic.Int32
	if err := api.ConfigureDesktopShutdown("127.0.0.1:18476", func() { stopped.Add(1) }); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("GET", "http://127.0.0.1:18476/api/health", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	var health map[string]any
	if json.Unmarshal(response.Body.Bytes(), &health) != nil || health["shutdown_token"] != api.desktopToken || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(response.Body.String())
	}
	for _, attempt := range []struct {
		host, origin, token string
		status              int
	}{
		{"127.0.0.1:18476", "", "", 403},
		{"127.0.0.1:18476", "http://evil.invalid", api.desktopToken, 403},
		{"127.0.0.1:18476", "http://127.0.0.1:18476/path", api.desktopToken, 403},
		{"127.0.0.1:18477", "", api.desktopToken, 403},
		{"127.0.0.1:18476", "http://127.0.0.1:18476", api.desktopToken, 200},
		{"127.0.0.1:18476", "", api.desktopToken, 200},
	} {
		request = httptest.NewRequest(http.MethodPost, "http://"+attempt.host+"/api/runtime/shutdown", nil)
		request.Header.Set("Origin", attempt.origin)
		request.Header.Set("X-NEU-Shutdown-Token", attempt.token)
		response = httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != attempt.status {
			t.Fatal(attempt, response.Code)
		}
	}
	deadline := time.Now().Add(time.Second)
	for stopped.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if stopped.Load() != 1 {
		t.Fatal("shutdown not requested exactly once", stopped.Load())
	}
}

func TestRemoteModeCannotConfigureShutdown(t *testing.T) {
	api, _ := testApplication(t, "remote-multi")
	if err := api.ConfigureDesktopShutdown("127.0.0.1:8000", func() {}); err == nil {
		t.Fatal("remote shutdown enabled")
	}
	response := httptest.NewRecorder()
	api.shutdownDesktop(response, httptest.NewRequest("POST", "http://127.0.0.1:8000/api/runtime/shutdown", nil))
	if response.Code != 404 {
		t.Fatal(response.Code)
	}
}

func TestDesktopInstallUsesConfirmedJob(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	if err := api.ConfigureDesktopShutdown("127.0.0.1:18476", func() { t.Error("unexpected shutdown") }); err != nil {
		t.Fatal(err)
	}
	api.releaseChecker.job = &runtimeUpdateJob{ID: "current", State: "downloaded", Path: "fixture.zip", SHA256: "fixture-digest"}
	calls := 0
	api.SetDesktopUpdateInstaller(func(archive, digest string) error {
		calls++
		if archive != "fixture.zip" || digest != "fixture-digest" {
			t.Fatal(archive, digest)
		}
		return nil
	})
	for _, attempt := range []struct {
		id, token string
		status    int
	}{{"current", "", 403}, {"old", api.desktopToken, 409}, {"current", api.desktopToken, 200}, {"current", api.desktopToken, 200}} {
		request := httptest.NewRequest("POST", "http://127.0.0.1:18476/api/runtime/update/"+attempt.id+"/install", nil)
		request.Header.Set("X-NEU-Shutdown-Token", attempt.token)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != attempt.status {
			t.Fatal(response.Code, response.Body.String())
		}
	}
	if calls != 1 {
		t.Fatal(calls)
	}
}
