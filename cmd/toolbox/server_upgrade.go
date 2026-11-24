package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"sync/atomic"
	"time"
)

func linuxStartupGate(next http.Handler, ready *atomic.Bool) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if !ready.Load() && (request.Method != http.MethodGet || (request.URL.Path != "/api/health" && request.URL.Path != "/")) {
			response.Header().Set("Cache-Control", "no-store")
			response.Header().Set("Retry-After", "1")
			http.Error(response, "upgrade health check in progress", http.StatusServiceUnavailable)
			return
		}
		next.ServeHTTP(response, request)
	})
}

func waitForLinuxActivation(ctx context.Context, finished <-chan error, marker string) (bool, error) {
	interval := time.NewTicker(100 * time.Millisecond)
	defer interval.Stop()
	for {
		select {
		case <-ctx.Done():
			return false, nil
		case err := <-finished:
			return false, err
		case <-interval.C:
			if ctx.Err() != nil {
				return false, nil
			}
			if _, err := os.Lstat(marker); errors.Is(err, os.ErrNotExist) {
				return true, nil
			} else if err != nil {
				return false, err
			}
		}
	}
}
