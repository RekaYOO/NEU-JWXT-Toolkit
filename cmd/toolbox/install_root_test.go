package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDesktopInstallRootTracksSelection(t *testing.T) {
	root := t.TempDir()
	name := ".toolbox-update-123"
	slot := filepath.Join(root, name)
	executable := filepath.Join(slot, "toolbox.exe")
	if err := os.Mkdir(slot, 0700); err != nil {
		t.Fatal(err)
	}
	checkDenied := func() {
		t.Helper()
		if _, err := desktopInstallRoot(executable); err == nil {
			t.Fatal("unexpected installer access")
		}
	}
	checkDenied()
	write := func(relative, value string) {
		t.Helper()
		path := filepath.Join(root, relative)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("VERSION", "1")
	write("frontend/build/index.html", "test")
	write("toolbox.exe", "test")
	checkDenied()
	write(activeReleaseFile, name+"\n")
	if got, err := desktopInstallRoot(executable); err != nil || got != root {
		t.Fatalf("root=%q error=%v", got, err)
	}
	write(activeReleaseFile, ".toolbox-update-456\n")
	checkDenied()
	if err := os.Remove(filepath.Join(root, activeReleaseFile)); err != nil {
		t.Fatal(err)
	}
	checkDenied()
}
