package main

import (
	"os"
	"path/filepath"
)

type runtimePaths struct {
	Bundled bool
	Icon    string
	Assets  string
	Version string
	Data    string
}

func resolveRuntimePaths(executable, workingDirectory, configDirectory string) runtimePaths {
	root := workingDirectory
	bundled := false
	if executable != "" {
		candidate := filepath.Dir(executable)
		version, versionErr := os.Stat(filepath.Join(candidate, "VERSION"))
		index, indexErr := os.Stat(filepath.Join(candidate, "frontend", "build", "index.html"))
		if versionErr == nil && version.Mode().IsRegular() && indexErr == nil && index.Mode().IsRegular() {
			root = candidate
			bundled = true
		}
	}
	data := filepath.Join(root, "data", "go")
	if bundled && configDirectory != "" {
		data = filepath.Join(configDirectory, "NEU-JWXT-Toolkit", "data-go")
	}
	icon := filepath.Join(root, "packaging", "windows", "app.ico")
	if bundled {
		icon = filepath.Join(root, "app.ico")
	}
	return runtimePaths{Assets: filepath.Join(root, "frontend", "build"), Version: filepath.Join(root, "VERSION"), Data: data, Bundled: bundled, Icon: icon}
}
