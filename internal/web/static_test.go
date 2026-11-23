package web

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStaticBoundaryAndEncoding(t *testing.T) {
	directory := t.TempDir()
	if err := os.MkdirAll(filepath.Join(directory, "static", "js"), 0700); err != nil {
		t.Fatal(err)
	}
	for name, content := range map[string]string{
		"index.html": "application shell", "asset-manifest.json": "{}",
		"static/js/main.12345678.js": "javascript", "static/js/main.12345678.js.br": "brotli", "static/js/main.12345678.js.gz": "gzip",
		"private.json": "secret",
	} {
		if err := os.WriteFile(filepath.Join(directory, filepath.FromSlash(name)), []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	handler, err := NewStatic(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer handler.Close()
	for _, route := range []string{"/private.json", "/api/scores", "/static/../private.json", "/static/%2e%2e/private.json", "/static/%252e%252e/private.json", "/static/missing.js"} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest("GET", route, nil))
		if response.Code != 404 {
			t.Fatalf("unsafe fallback %s: %d", route, response.Code)
		}
	}
	request := httptest.NewRequest("GET", "/static/js/main.12345678.js", nil)
	request.Header.Set("Accept-Encoding", "br;q=0.1,gzip;q=0.9")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Body.String() != "gzip" || response.Header().Get("Content-Encoding") != "gzip" || !strings.Contains(response.Header().Get("Cache-Control"), "immutable") {
		t.Fatalf("invalid encoding negotiation: %+v", response)
	}
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/course-selection/round", nil))
	if response.Body.String() != "application shell" || response.Header().Get("Cache-Control") != "no-cache" {
		t.Fatal("deep link did not serve revalidated shell")
	}
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/auth-recovery/"+strings.Repeat("a", 64)+"."+strings.Repeat("b", 64), nil))
	if response.Body.String() != "application shell" || response.Header().Get("Cache-Control") != "no-store" || response.Header().Get("Referrer-Policy") != "no-referrer" {
		t.Fatal("signed recovery deep link missing or exposed referrer")
	}
}
