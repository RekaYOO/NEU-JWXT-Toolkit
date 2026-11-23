package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRuntimePathsFollowBundleRatherThanWorkingDirectory(t *testing.T) {
	root := t.TempDir()
	bundle := filepath.Join(root, "bundle")
	working := filepath.Join(root, "elsewhere")
	config := filepath.Join(root, "user-data")
	if err := os.MkdirAll(filepath.Join(bundle, "frontend", "build"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"VERSION", "frontend/build/index.html"} {
		if err := os.WriteFile(filepath.Join(bundle, name), []byte("fixture"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	paths := resolveRuntimePaths(filepath.Join(bundle, "toolbox.exe"), working, config)
	if paths.Version != filepath.Join(bundle, "VERSION") || paths.Assets != filepath.Join(bundle, "frontend", "build") || paths.Data != filepath.Join(config, "NEU-JWXT-Toolkit", "data-go") {
		t.Fatal(paths)
	}
	development := resolveRuntimePaths(filepath.Join(root, "dist", "toolbox.exe"), working, config)
	if development.Version != filepath.Join(working, "VERSION") || development.Data != filepath.Join(working, "data", "go") {
		t.Fatal(development)
	}
}
