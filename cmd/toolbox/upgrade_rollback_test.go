package main

import (
	"archive/zip"
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestDesktopStartupFailureRestoresAndRelaunches(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows launcher")
	}
	root, data := t.TempDir(), t.TempDir()
	const slot = ".toolbox-update-123"
	for name, content := range map[string]string{"VERSION": "previous", "frontend/build/index.html": "old", "frontend/build/asset-manifest.json": "{}", activeReleaseFile: slot, slot + "/VERSION": "candidate", slot + "/frontend/build/index.html": "new"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	exe := filepath.Join(root, "toolbox.exe")
	if out, err := exec.Command(filepath.Join(runtime.GOROOT(), "bin", "go.exe"), "build", "-o", exe, ".").CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	priorSlot := ""
	if archive := os.Getenv("TOOLBOX_ROLLBACK_NEW_ZIP"); archive != "" {
		priorSlot = ".toolbox-update-122"
		for name, content := range map[string]string{slot + ".previous": priorSlot, priorSlot + "/VERSION": "previous", priorSlot + "/frontend/build/index.html": "old", priorSlot + "/frontend/build/asset-manifest.json": "{}"} {
			path := filepath.Join(root, name)
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte(content), 0600); err != nil {
				t.Fatal(err)
			}
		}
		for source, destination := range map[string]string{archive: slot, os.Getenv("TOOLBOX_ROLLBACK_OLD_ZIP"): priorSlot} {
			bundle, err := zip.OpenReader(source)
			if err != nil {
				t.Fatal(err)
			}
			defer bundle.Close()
			found := false
			for _, entry := range bundle.File {
				if filepath.Base(entry.Name) != "toolbox.exe" {
					continue
				}
				reader, err := entry.Open()
				if err != nil {
					t.Fatal(err)
				}
				output, err := os.Create(filepath.Join(root, destination, "toolbox.exe"))
				if err != nil {
					t.Fatal(err)
				}
				_, err = io.Copy(output, reader)
				closeErr := output.Close()
				reader.Close()
				if err != nil || closeErr != nil {
					t.Fatal(err, closeErr)
				}
				found = true
				break
			}
			if !found {
				t.Fatal("archive missing executable")
			}
		}
	} else if err := os.Link(exe, filepath.Join(root, slot, "toolbox.exe")); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	db, err := store.Open(data)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AddAccount(ctx, "original-account"); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	command := exec.CommandContext(ctx, exe, "launch", "-listen=127.0.0.1:0", "-tray=false", "-open-browser=false", "-mode=local-single")
	command.Env = append(os.Environ(), "NEU_JWXT_DATA_DIR="+data)
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	for {
		select {
		case err := <-done:
			t.Fatal("launcher exited before previous version became healthy", err)
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(75 * time.Millisecond):
		}
		if endpoint, err := existingDesktop(data); err == nil {
			if err := stopReleaseProbe(ctx, endpoint, "previous"); err != nil {
				t.Fatal(err)
			}
			break
		}
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if priorSlot != "" {
		selected, err := os.ReadFile(filepath.Join(root, activeReleaseFile))
		if err != nil || strings.TrimSpace(string(selected)) != priorSlot {
			t.Fatal("did not reselect prior release", err)
		}
	} else if _, err := os.Stat(filepath.Join(root, activeReleaseFile)); !os.IsNotExist(err) {
		t.Fatal("candidate remained selected", err)
	}
	db, err = store.Open(data)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Generation(ctx, "original-account"); err != nil {
		t.Fatal(err)
	}
}
