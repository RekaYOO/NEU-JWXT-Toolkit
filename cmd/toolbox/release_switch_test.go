package main

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestReleaseDataProbeUsesCandidateExecutable(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows release layout")
	}
	stage := t.TempDir()
	build := exec.Command(filepath.Join(runtime.GOROOT(), "bin", "go.exe"), "build", "-o", filepath.Join(stage, "toolbox.exe"), ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatal(err, string(output))
	}
	directory := filepath.Join(t.TempDir(), "existing data")
	database, err := store.Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if err := probeReleaseData(stage, directory); err != nil {
		t.Fatal(err)
	}
	missing := filepath.Join(t.TempDir(), "absent")
	if err := probeReleaseData(stage, missing); err == nil {
		t.Fatal("accepted missing database")
	}
	if _, err := os.Stat(missing); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("probe created missing source", err)
	}
}

func TestDesktopReleaseStartFallback(t *testing.T) {
	original, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	missing := filepath.Join(t.TempDir(), "missing.exe")
	if err := startDesktopRelease(missing, original, []string{"-test.run=^$"}); err != nil {
		t.Fatal(err)
	}
	var exit *exec.ExitError
	err = startDesktopRelease(original, missing, []string{"-invalid-test-option"})
	if !errors.As(err, &exit) || errors.Is(err, os.ErrNotExist) {
		t.Fatalf("expected only selected process exit error: %v", err)
	}
}

func TestWindowsReleaseSwitchFromArchive(t *testing.T) {
	archive := os.Getenv("TOOLBOX_TEST_RELEASE_ZIP")
	if runtime.GOOS != "windows" || archive == "" {
		t.Skip("requires Windows release fixture")
	}
	root := t.TempDir()
	exe := filepath.Join(root, "toolbox.exe")
	goTool := filepath.Join(runtime.GOROOT(), "bin", "go.exe")
	if out, err := exec.Command(goTool, "build", "-o", exe, ".").CombinedOutput(); err != nil {
		t.Fatal(err, string(out))
	}
	if err := os.MkdirAll(filepath.Join(root, "frontend", "build"), 0700); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{"VERSION": "baseline", "frontend/build/index.html": "baseline"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	run := func(args ...string) string {
		t.Helper()
		out, err := exec.Command(exe, args...).Output()
		if err != nil {
			t.Fatal(args, err)
		}
		return strings.TrimSpace(string(out))
	}
	run("install-update", "-archive", archive, "-sha256", os.Getenv("TOOLBOX_TEST_RELEASE_SHA256"))
	version := os.Getenv("TOOLBOX_TEST_RELEASE_VERSION")
	if got := run("launch", "-version"); got != version {
		t.Fatal(got)
	}
	marker := filepath.Join(root, activeReleaseFile)
	slot, err := os.ReadFile(marker)
	if err != nil {
		t.Fatal(err)
	}
	name := strings.TrimSpace(string(slot))
	if !slotName.MatchString(name) {
		t.Fatal(name)
	}
	source := filepath.Join(t.TempDir(), "existing data")
	database, err := store.Open(source)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.AddAccount(context.Background(), "upgrade-fixture"); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	incomplete := filepath.Join(source, ".restore-in-progress")
	if err := os.WriteFile(incomplete, []byte("test"), 0600); err != nil {
		t.Fatal(err)
	}
	rejection, rejected := exec.Command(exe, "launch", "install-update", "-archive", archive, "-sha256", os.Getenv("TOOLBOX_TEST_RELEASE_SHA256"), "-check-data", source).CombinedOutput()
	if rejected == nil || !strings.Contains(string(rejection), "未通过现有数据副本") {
		t.Fatalf("missing data rejection: %s (%v)", rejection, rejected)
	}
	afterCheck, err := os.ReadFile(marker)
	if err != nil || string(afterCheck) != string(slot) {
		t.Fatal("rejected data check changed selection", err)
	}
	if err := os.Remove(incomplete); err != nil {
		t.Fatal(err)
	}
	database, err = store.Open(source)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Generation(context.Background(), "upgrade-fixture"); err != nil {
		t.Fatal("original account unavailable", err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	broken := filepath.Join(t.TempDir(), filepath.Base(archive))
	if err := os.WriteFile(broken, []byte("PK\x03\x04"), 0600); err != nil {
		t.Fatal(err)
	}
	output, installErr := exec.Command(exe, "launch", "install-update", "-archive", broken, "-sha256", os.Getenv("TOOLBOX_TEST_RELEASE_SHA256")).CombinedOutput()
	if installErr == nil || !strings.Contains(string(output), "archive digest mismatch") {
		t.Fatalf("expected digest rejection: %s (%v)", output, installErr)
	}
	retained, err := os.ReadFile(marker)
	if err != nil || string(retained) != string(slot) {
		t.Fatalf("failed update changed active installation: %q (%v)", retained, err)
	}
	if got := run("launch", "-version"); got != version {
		t.Fatalf("prior release unavailable after rejected update: %s", got)
	}
	run("launch", "install-update", "-archive", archive, "-sha256", os.Getenv("TOOLBOX_TEST_RELEASE_SHA256"))
	next, err := os.ReadFile(marker)
	if err != nil {
		t.Fatal(err)
	}
	nextName := strings.TrimSpace(string(next))
	if !slotName.MatchString(nextName) || nextName == name {
		t.Fatal("second installation did not switch the original launcher", nextName)
	}
	if _, err := os.Stat(filepath.Join(root, name, activeReleaseFile)); !os.IsNotExist(err) {
		t.Fatal("nested launcher selection", err)
	}
	if got := run("launch", "-version"); got != version {
		t.Fatal(got)
	}
	name = nextName
	if err := os.Remove(filepath.Join(root, name, "toolbox.exe")); err != nil {
		t.Fatal(err)
	}
	if got := run("launch", "-version"); got != version {
		t.Fatal("failed to select previous installed version", got)
	}
	if err := os.Remove(filepath.Join(root, strings.TrimSpace(string(slot)), "toolbox.exe")); err != nil {
		t.Fatal(err)
	}
	if got := run("launch", "-version"); got != "baseline" {
		t.Fatal(got)
	}
	run("launch", "rollback-update")
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal(err)
	}
	if got := run("launch", "-version"); got != "baseline" {
		t.Fatal(got)
	}
}
