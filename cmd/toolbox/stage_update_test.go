package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type unavailableStageOutput struct{}

func (unavailableStageOutput) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }

func TestStageUpdateFailureLeavesInstallationAlone(t *testing.T) {
	for _, reason := range []string{"digest", "version", "output"} {
		t.Run(reason, func(t *testing.T) {
			parent := t.TempDir()
			previous := filepath.Join(parent, "previous-installation")
			if err := os.WriteFile(previous, []byte("unchanged"), 0600); err != nil {
				t.Fatal(err)
			}
			bundle := "NEU-JWXT-Toolkit-2.2.4-windows-amd64-go"
			var payload bytes.Buffer
			archive := zip.NewWriter(&payload)
			version := "2.2.4"
			if reason == "version" {
				version = "2.2.3"
			}
			for name, content := range map[string]string{"VERSION": version, "toolbox.exe": "program", "frontend/build/index.html": "page"} {
				entry, err := archive.Create(bundle + "/" + name)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := io.WriteString(entry, content); err != nil {
					t.Fatal(err)
				}
			}
			if err := archive.Close(); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(t.TempDir(), bundle+".zip")
			if err := os.WriteFile(path, payload.Bytes(), 0600); err != nil {
				t.Fatal(err)
			}
			digest := fmt.Sprintf("%x", sha256.Sum256(payload.Bytes()))
			if reason == "digest" {
				digest = strings.Repeat("0", 64)
			}
			var output io.Writer = io.Discard
			if reason == "output" {
				output = unavailableStageOutput{}
			}
			err := stageWindowsUpdate([]string{"-parent", parent, "-archive", path, "-sha256", digest}, output)
			if err == nil {
				t.Fatal("expected staging failure")
			}
			entries, err := os.ReadDir(parent)
			if err != nil || len(entries) != 1 {
				t.Fatalf("unexpected files after failure: %v (%v)", entries, err)
			}
			value, err := os.ReadFile(previous)
			if err != nil || string(value) != "unchanged" {
				t.Fatal("previous installation changed", err)
			}
		})
	}
}
