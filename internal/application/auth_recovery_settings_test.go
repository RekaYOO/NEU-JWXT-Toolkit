package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestRecoverySettingsIsolationValidationAndEpoch(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "20990001")
	api.database.AddAccount(ctx, "20990002")
	cookie := loginCookie(t, handler, "20990001")
	other := loginCookie(t, handler, "20990002")
	path := "/api/system-settings/auth-recovery"
	for _, body := range []string{`{"public_base_url":"http://public.example.invalid"}`, `{"public_base_url":"https://user:pass@example.invalid"}`, `{"public_base_url":"https://example.invalid/path"}`, `{"public_base_url":"https://example.invalid?secret=x"}`, `{"link_ttl_hours":0}`, `{"link_ttl_hours":169}`, `{"link_ttl_hours":2.5}`} {
		if response := apiRequest(handler, "PUT", path, body, cookie); response.Code < 400 {
			t.Fatal("invalid configuration accepted", body)
		}
	}
	response := apiRequest(handler, "PUT", path, `{"public_base_url":"https://toolbox.example.invalid/"}`, cookie)
	if response.Code != 200 || strings.Contains(response.Body.String(), "epoch") {
		t.Fatal(response)
	}
	session := store.Session{Account: "20990001", Generation: 1}
	first, _, err := api.readRecoverySettings(ctx, session)
	if err != nil || first.Hours != 3 || first.Epoch == "" {
		t.Fatal(first, err)
	}
	recovery, err := api.database.CreateRecoveryContext(ctx, session, "primary", "tracking", "lost", first.Epoch, 3*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	token, err := api.database.RecoveryToken(ctx, recovery.ID)
	if err != nil {
		t.Fatal(err)
	}
	if response := apiRequest(handler, "PUT", path, `{"link_ttl_hours":4}`, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	updated, _, _ := api.readRecoverySettings(ctx, session)
	if updated.Epoch != first.Epoch || updated.BaseURL != first.BaseURL || updated.Hours != 4 {
		t.Fatal("partial update lost configuration or changed epoch")
	}
	if current, err := api.database.RecoveryContext(ctx, token); err != nil || current.Expires != recovery.Expires {
		t.Fatal("TTL edit modified existing link", err)
	}
	for _, base := range []string{"https://changed.example.invalid", first.BaseURL} {
		body, _ := json.Marshal(map[string]string{"public_base_url": base})
		if response := apiRequest(handler, "PUT", path, string(body), cookie); response.Code != 200 {
			t.Fatal(response)
		}
		if _, err := api.database.RecoveryContext(ctx, token); err == nil {
			t.Fatal("site change/revert revived old link")
		}
	}
	response = apiRequest(handler, "GET", path, "", other)
	if response.Code != 200 || strings.Contains(response.Body.String(), "toolbox.example.invalid") {
		t.Fatal("settings leaked between accounts", response)
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if response := apiRequest(handler, method, "/api/documents/auth-recovery/config", `{"data":{},"revision":4}`, cookie); response.Code != 403 {
			t.Fatal("generic document write bypassed validation", response)
		}
	}
}

func TestRecoveryBaseURLPolicy(t *testing.T) {
	for _, value := range []string{"http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"} {
		if !validRecoveryBase(value, false) || validRecoveryBase(value, true) {
			t.Fatal("loopback HTTP policy mismatch")
		}
	}
	for _, value := range []string{"javascript:alert(1)", "https://", "https://example.invalid?", "https://example.invalid/#secret", "https://example.invalid#", "https://example.invalid:", "https://example.invalid:99999", "http://example.invalid", "//example.invalid"} {
		if validRecoveryBase(value, false) || validRecoveryBase(value, true) {
			t.Fatal("unsafe base URL accepted")
		}
	}
}
