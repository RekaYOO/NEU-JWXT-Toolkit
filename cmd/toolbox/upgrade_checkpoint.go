package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/processlock"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func upgradeStartupGate(next http.Handler, ready *atomic.Bool) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if !ready.Load() && (request.Method != http.MethodGet || request.URL.Path != "/api/health") {
			response.Header().Set("Retry-After", "1")
			http.Error(response, "upgrade health check in progress", http.StatusServiceUnavailable)
			return
		}
		next.ServeHTTP(response, request)
	})
}

func checkpointMarker(directory, executable string) string {
	stage := strings.ToLower(filepath.Dir(executable))
	return filepath.Join(directory, fmt.Sprintf(".upgrade-checkpoint-%x", sha256.Sum256([]byte(stage))))
}

func restoreUpgradeCheckpoint(ctx context.Context, directory, executable string) error {
	lock, err := processlock.Acquire(directory)
	if err != nil {
		return err
	}
	defer lock.Close()
	return restoreLockedUpgradeCheckpoint(ctx, directory, executable)
}

func restoreLockedUpgradeCheckpoint(ctx context.Context, directory, executable string) error {
	marker := checkpointMarker(directory, executable)
	if _, err := os.Lstat(marker + ".healthy"); !errors.Is(err, os.ErrNotExist) {
		return errors.New("cannot restore a confirmed upgrade")
	}
	name, err := os.ReadFile(marker)
	if err != nil {
		return err
	}
	if filepath.Base(string(name)) != string(name) || !strings.HasPrefix(string(name), ".toolbox-upgrade-snapshot-") {
		return errors.New("invalid checkpoint name")
	}
	snapshot := filepath.Join(directory, string(name))
	info, err := os.Lstat(snapshot)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("invalid checkpoint directory")
	}
	for _, path := range []string{filepath.Join(snapshot, "toolbox.db"), filepath.Join(snapshot, "vault.key"), filepath.Join(directory, "vault.key"), filepath.Join(directory, "toolbox.db")} {
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() {
			return errors.New("invalid rollback source or target")
		}
	}
	for _, name := range []string{"toolbox.db-wal", "toolbox.db-shm", "toolbox.db-journal", ".restore-in-progress"} {
		if _, err := os.Lstat(filepath.Join(directory, name)); !errors.Is(err, os.ErrNotExist) {
			return errors.New("database not ready for rollback")
		}
	}
	originalKey, err := os.ReadFile(filepath.Join(snapshot, "vault.key"))
	if err != nil {
		return err
	}
	currentKey, err := os.ReadFile(filepath.Join(directory, "vault.key"))
	if err != nil || len(currentKey) != 32 || !bytes.Equal(currentKey, originalKey) {
		return errors.New("rollback key mismatch")
	}
	prepared, err := store.CreateUpgradeSnapshot(ctx, snapshot, directory)
	if err != nil {
		return err
	}
	defer os.RemoveAll(prepared)
	if err := os.Rename(filepath.Join(prepared, "toolbox.db"), filepath.Join(directory, "toolbox.db")); err != nil {
		return err
	}
	if err := os.Remove(marker); err != nil {
		return err
	}
	return removeUpgradeSnapshot(snapshot)
}

func confirmUpgradeCheckpoint(directory, executable, endpoint, version string, prepare func() error) error {
	marker := checkpointMarker(directory, executable)
	name, err := os.ReadFile(marker)
	if errors.Is(err, os.ErrNotExist) {
		return prepare()
	}
	if err != nil {
		return err
	}
	if filepath.Base(string(name)) != string(name) || !strings.HasPrefix(string(name), ".toolbox-upgrade-snapshot-") {
		return errors.New("invalid checkpoint name")
	}
	snapshot := filepath.Join(directory, string(name))
	info, err := os.Lstat(snapshot)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("invalid checkpoint directory")
	}
	client := http.Client{Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Get(endpoint + "/api/health")
	if err != nil {
		return err
	}
	defer response.Body.Close()
	var health struct{ Backend, Version string }
	if response.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&health) != nil || health.Backend != "go" || health.Version != version {
		return errors.New("upgrade health confirmation failed")
	}
	if err := prepare(); err != nil {
		return err
	}
	if err := os.Rename(marker, marker+".healthy"); err != nil {
		return err
	}
	return removeUpgradeSnapshot(snapshot)
}

func removeUpgradeSnapshot(snapshot string) error {
	for _, file := range []string{"toolbox.db", "vault.key", "toolbox.db-wal", "toolbox.db-shm"} {
		if err := os.Remove(filepath.Join(snapshot, file)); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return os.Remove(snapshot)
}

func registerUpgradeCheckpoint(ctx context.Context, directory, executable string) error {
	if _, err := os.Stat(filepath.Join(directory, "toolbox.db")); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	stage := filepath.Dir(executable)
	if !slotName.MatchString(filepath.Base(stage)) {
		return nil
	}
	if _, err := desktopInstallRoot(executable); err != nil {
		return err
	}
	marker := checkpointMarker(directory, executable)
	if info, err := os.Lstat(marker + ".healthy"); err == nil && info.Mode().IsRegular() {
		return nil
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if existing, err := os.ReadFile(marker); err == nil {
		name := string(existing)
		if filepath.Base(name) != name || !strings.HasPrefix(name, ".toolbox-upgrade-snapshot-") {
			return errors.New("invalid upgrade checkpoint registration")
		}
		for _, file := range []string{"toolbox.db", "vault.key"} {
			info, err := os.Lstat(filepath.Join(directory, name, file))
			if err != nil || !info.Mode().IsRegular() {
				return errors.New("registered upgrade checkpoint is unavailable")
			}
		}
		return nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	snapshot, err := store.CreateUpgradeSnapshot(ctx, directory, directory)
	if err != nil {
		return err
	}
	registration, err := os.CreateTemp(directory, ".checkpoint-registration-")
	if err != nil {
		return errors.Join(err, os.RemoveAll(snapshot))
	}
	defer os.Remove(registration.Name())
	_, writeErr := registration.WriteString(filepath.Base(snapshot))
	if err := errors.Join(writeErr, registration.Sync(), registration.Close()); err != nil {
		return errors.Join(err, os.RemoveAll(snapshot))
	}
	if err := os.Rename(registration.Name(), marker); err != nil {
		return errors.Join(err, os.RemoveAll(snapshot))
	}
	return nil
}
