package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestDesktopInstanceRequiresGoLoopbackService(t *testing.T) {
	directory := t.TempDir()
	backend := "go"
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/health" {
			t.Error("unexpected probe", request.URL.Path)
		}
		response.Header().Set("Content-Type", "application/json")
		response.Write([]byte(`{"backend":"` + backend + `","profile":"desktop"}`))
	}))
	defer server.Close()
	if err := saveDesktopInstance(directory, server.URL); err != nil {
		t.Fatal(err)
	}
	if target, err := existingDesktop(directory); err != nil || target != server.URL {
		t.Fatal(target, err)
	}
	backend = "other"
	if _, err := existingDesktop(directory); err == nil {
		t.Fatal("unrelated service accepted")
	}
	for _, target := range []string{"https://example.com:8000", "http://user@127.0.0.1:8000", "http://127.0.0.1:8000/other", "http://127.0.0.1:8000?url=bad"} {
		if err := saveDesktopInstance(directory, target); err != nil {
			t.Fatal(err)
		}
		if _, err := existingDesktop(directory); err == nil {
			t.Fatal("unsafe existing URL", target)
		}
	}
}
