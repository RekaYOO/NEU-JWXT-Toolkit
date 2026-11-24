package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func TestLinuxUpgradeGate(t *testing.T) {
	var ready atomic.Bool
	handler := linuxStartupGate(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		response.WriteHeader(http.StatusNoContent)
	}), &ready)
	for _, test := range []struct {
		method string
		path   string
		status int
	}{
		{http.MethodGet, "/api/health", http.StatusNoContent},
		{http.MethodGet, "/", http.StatusNoContent},
		{http.MethodGet, "/api/access/status", http.StatusServiceUnavailable},
		{http.MethodPost, "/api/access/login", http.StatusServiceUnavailable},
		{http.MethodPost, "/api/health", http.StatusServiceUnavailable},
	} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(test.method, test.path, nil))
		if response.Code != test.status {
			t.Fatalf("%s %s returned %d, want %d", test.method, test.path, response.Code, test.status)
		}
	}
	ready.Store(true)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/api/access/login", nil))
	if response.Code != http.StatusNoContent {
		t.Fatalf("activated request returned %d", response.Code)
	}
}

func TestLinuxUpgradeMarker(t *testing.T) {
	marker := filepath.Join(t.TempDir(), ".upgrade-pending")
	if err := os.WriteFile(marker, nil, 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	if active, err := waitForLinuxActivation(ctx, nil, marker); active || err != nil {
		t.Fatalf("pending marker must keep the startup gate closed: %v, %v", active, err)
	}
	if err := os.Remove(marker); err != nil {
		t.Fatal(err)
	}
	ctx, cancel = context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if active, err := waitForLinuxActivation(ctx, nil, marker); !active || err != nil {
		t.Fatalf("removed marker must open the startup gate: %v, %v", active, err)
	}
}
