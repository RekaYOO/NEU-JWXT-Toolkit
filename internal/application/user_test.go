package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestAvatarCacheIdentityAndBootstrap(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(context.Background(), account); err != nil {
			t.Fatal(err)
		}
	}
	cookieA := loginCookie(t, handler, "student-a")
	cookieB := loginCookie(t, handler, "student-b")
	session, _ := api.database.Session(context.Background(), cookieA.Value)
	missing := apiRequest(handler, "GET", "/api/user/avatar/cache", "", cookieA)
	if missing.Code != 204 || missing.Header().Get("X-Cache-Miss") != "true" {
		t.Fatal(missing)
	}
	api.resources.mutex.Lock()
	jobs := len(api.resources.jobs)
	api.resources.mutex.Unlock()
	if jobs != 0 {
		t.Fatal("cache-only request started a refresh")
	}
	entry := store.CacheEntry{Resource: "avatar", Variant: "default", SchemaVersion: 1, Revision: "photo-revision", Payload: json.RawMessage(`{"image_base64":"R0lGODlh","media_type":"image/gif","token":"photo"}`)}
	if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
		t.Fatal(err)
	}
	response := apiRequest(handler, "GET", "/api/user/avatar/cache", "", cookieA)
	if response.Code != 200 || response.Body.String() != "GIF89a" || response.Header().Get("Content-Type") != "image/gif" || response.Header().Get("X-Avatar-Token") != "photo" || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(response)
	}
	bootstrap := apiRequest(handler, "GET", "/api/client/bootstrap", "", cookieA)
	if !strings.Contains(bootstrap.Body.String(), `"image_base64":"R0lGODlh"`) {
		t.Fatal(bootstrap.Body.String())
	}
	if response := apiRequest(handler, "GET", "/api/user/avatar/cache", "", cookieB); response.Code != 204 {
		t.Fatal("cross-account avatar leak")
	}
	if response := apiRequest(handler, "GET", "/api/user/avatar/cache", "", nil); response.Code != 401 {
		t.Fatal("anonymous avatar leak")
	}
	apiRequest(handler, "POST", "/api/logout", "", cookieA)
	if response := apiRequest(handler, "GET", "/api/user/avatar/cache", "", cookieA); response.Code != 401 {
		t.Fatal("logged out avatar leak")
	}
}
