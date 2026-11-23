package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

func stageWindowsUpdate(args []string, output io.Writer) error {
	flags := flag.NewFlagSet("stage-update", flag.ContinueOnError)
	archive := flags.String("archive", "", "Windows Go release ZIP")
	digest := flags.String("sha256", "", "expected digest from the official release manifest")
	parent := flags.String("parent", "", "existing directory for the new installation")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *parent == "" || len(*digest) != 64 {
		return errors.New("stage-update requires -archive, -sha256 and -parent")
	}
	return prepareWindowsUpdate(*archive, *digest, *parent, func(stage string) error {
		_, err := fmt.Fprintln(output, stage)
		return err
	})
}

func prepareWindowsUpdate(archive, digest, parent string, publish func(string) error) error {
	file, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() > 1<<30 {
		return errors.New("invalid archive size or type")
	}
	hash := sha256.New()
	if _, err = io.Copy(hash, io.LimitReader(file, info.Size()+1)); err != nil {
		return err
	}
	if hex.EncodeToString(hash.Sum(nil)) != digest {
		return errors.New("archive digest mismatch")
	}
	bundle := strings.TrimSuffix(filepath.Base(archive), ".zip")
	version, ok := strings.CutPrefix(bundle, "NEU-JWXT-Toolkit-")
	version, suffix := strings.CutSuffix(version, "-windows-amd64-go")
	if !ok || !suffix || version == "" {
		return errors.New("invalid Windows release name")
	}
	reader, err := zip.NewReader(file, info.Size())
	if err != nil {
		return err
	}
	if len(reader.File) > 8192 {
		return errors.New("too many release files")
	}
	stage, err := os.MkdirTemp(parent, ".toolbox-update-")
	if err != nil {
		return err
	}
	ready := false
	defer func() {
		if !ready {
			os.RemoveAll(stage)
		}
	}()
	root, err := os.OpenRoot(stage)
	if err != nil {
		return err
	}
	defer root.Close()
	remaining := uint64(2 << 30)
	for _, entry := range reader.File {
		name, inside := strings.CutPrefix(entry.Name, bundle+"/")
		local, pathErr := filepath.Localize(name)
		if !inside || !fs.ValidPath(name) || pathErr != nil || strings.ContainsAny(name, `:\`) || !entry.Mode().IsRegular() || entry.UncompressedSize64 > remaining {
			return errors.New("invalid release entry")
		}
		remaining -= entry.UncompressedSize64
		if err = makeUpdateDirs(root, filepath.Dir(local)); err != nil {
			return err
		}
		if err = extractUpdateFile(root, local, entry); err != nil {
			return err
		}
	}
	value, err := fs.ReadFile(root.FS(), "VERSION")
	if err != nil || strings.TrimSpace(string(value)) != version {
		return errors.New("release version mismatch")
	}
	for _, name := range []string{"toolbox.exe", "frontend/build/index.html"} {
		info, err := root.Stat(name)
		if err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
			return errors.New("incomplete Windows release")
		}
	}
	err = publish(stage)
	ready = err == nil
	return err
}

func makeUpdateDirs(root *os.Root, dir string) error {
	if dir == "." {
		return nil
	}
	if err := makeUpdateDirs(root, filepath.Dir(dir)); err != nil {
		return err
	}
	err := root.Mkdir(dir, 0700)
	if errors.Is(err, os.ErrExist) {
		info, statErr := root.Lstat(dir)
		if statErr == nil && info.IsDir() {
			return nil
		}
	}
	return err
}

func extractUpdateFile(root *os.Root, name string, entry *zip.File) error {
	source, err := entry.Open()
	if err != nil {
		return err
	}
	defer source.Close()
	target, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	count, copyErr := io.Copy(target, io.LimitReader(source, int64(entry.UncompressedSize64)+1))
	closeErr := target.Close()
	if copyErr != nil {
		return copyErr
	}
	if closeErr != nil {
		return closeErr
	}
	if uint64(count) != entry.UncompressedSize64 {
		return errors.New("release entry length mismatch")
	}
	return nil
}
