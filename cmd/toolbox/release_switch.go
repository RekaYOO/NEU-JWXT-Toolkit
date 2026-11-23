package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

const activeReleaseFile = ".toolbox-active-release"

var slotName = regexp.MustCompile(`^\.toolbox-update-[0-9]+$`)

func probeRelease(directory string) error {
	version, err := os.ReadFile(filepath.Join(directory, "VERSION"))
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result, err := exec.CommandContext(ctx, filepath.Join(directory, "toolbox.exe"), "-version").Output()
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(version)) == "" || strings.TrimSpace(string(result)) != strings.TrimSpace(string(version)) {
		return errors.New("release version probe failed")
	}
	return nil
}

func stopReleaseProbe(ctx context.Context, endpoint, version string) error {
	client := http.Client{Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint+"/api/health", nil)
	if err != nil {
		return err
	}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	var health struct {
		Version string `json:"version"`
		Token   string `json:"shutdown_token"`
	}
	err = json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&health)
	response.Body.Close()
	if err != nil || response.StatusCode != 200 || health.Version != version || len(health.Token) != 64 {
		return errors.New("unexpected release health response")
	}
	request, err = http.NewRequestWithContext(ctx, http.MethodPost, endpoint+"/api/runtime/shutdown", nil)
	if err != nil {
		return err
	}
	request.Header.Set("Origin", endpoint)
	request.Header.Set("X-NEU-Shutdown-Token", health.Token)
	response, err = client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	var result struct {
		Success bool `json:"success"`
	}
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&result) != nil || !result.Success {
		return errors.New("release shutdown was not acknowledged")
	}
	return nil
}

func probeReleaseService(directory string) (result error) {
	version, err := os.ReadFile(filepath.Join(directory, "VERSION"))
	if err != nil {
		return err
	}
	data, err := os.MkdirTemp("", "toolbox-release-check-")
	if err != nil {
		return err
	}
	defer func() { result = errors.Join(result, os.RemoveAll(data)) }()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, filepath.Join(directory, "toolbox.exe"), "-mode=local-single", "-listen=127.0.0.1:0", "-tray=false", "-open-browser=false", "-data", data)
	command.Dir = directory
	command.Env = []string{"NEU_JWXT_DATA_DIR=" + data, "LOCALAPPDATA=" + data}
	for _, key := range []string{"SystemRoot", "TEMP", "TMP"} {
		if value := os.Getenv(key); value != "" {
			command.Env = append(command.Env, key+"="+value)
		}
	}
	if err := command.Start(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case err := <-done:
			return fmt.Errorf("release exited before desktop health check: %v", err)
		case <-ctx.Done():
			<-done
			return ctx.Err()
		case <-ticker.C:
			if endpoint, err := existingDesktop(data); err == nil {
				if err := stopReleaseProbe(ctx, endpoint, strings.TrimSpace(string(version))); err != nil {
					cancel()
					<-done
					return err
				}
				return <-done
			}
		}
	}
}

func desktopInstallRoot(executable string) (string, error) {
	directory := filepath.Dir(executable)
	if !slotName.MatchString(filepath.Base(directory)) {
		return directory, nil
	}
	root := filepath.Dir(directory)
	resolved, err := filepath.EvalSymlinks(directory)
	if err != nil || !strings.EqualFold(resolved, directory) || slotName.MatchString(filepath.Base(root)) || !resolveRuntimePaths(filepath.Join(root, "toolbox.exe"), "", "").Bundled {
		return "", errors.New("invalid desktop installation root")
	}
	for _, name := range []string{"toolbox.exe", activeReleaseFile} {
		info, err := os.Lstat(filepath.Join(root, name))
		if err != nil || !info.Mode().IsRegular() {
			return "", errors.New("invalid desktop installation entry")
		}
	}
	selected, err := os.ReadFile(filepath.Join(root, activeReleaseFile))
	if err != nil || strings.TrimSpace(string(selected)) != filepath.Base(directory) {
		return "", errors.New("desktop release is no longer selected")
	}
	return root, nil
}

func probeReleaseData(stage, directory string) (result error) {
	source, err := filepath.Abs(directory)
	if err != nil {
		return err
	}
	scratch, err := os.MkdirTemp("", "toolbox-upgrade-probe-")
	if err != nil {
		return err
	}
	defer func() { result = errors.Join(result, os.RemoveAll(scratch)) }()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	command := exec.CommandContext(ctx, filepath.Join(stage, "toolbox.exe"), "check-upgrade", source)
	command.Dir = scratch
	command.Env = []string{"NEU_JWXT_DATA_DIR=" + scratch, "LOCALAPPDATA=" + scratch, "TEMP=" + scratch, "TMP=" + scratch, "SystemRoot=" + os.Getenv("SystemRoot"), "NEU_JWXT_MODE=upgrade-check-only"}
	output, err := command.Output()
	if err != nil || strings.TrimSpace(string(output)) != "toolbox-upgrade-check:ok" {
		return errors.New("新版未通过现有数据副本的迁移检查，已保留原启动选择")
	}
	return nil
}

func desktopReleaseCommand(action string, args []string) error {
	return runDesktopReleaseCommand(action, args, false)
}

func runDesktopReleaseCommand(action string, args []string, webInstall bool) error {
	if runtime.GOOS != "windows" {
		return errors.New("desktop release switching requires Windows")
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	root := filepath.Dir(exe)
	if action == "launch" && len(args) > 0 && (args[0] == "install-update" || args[0] == "rollback-update") {
		action, args = args[0], args[1:]
	}
	if slotName.MatchString(filepath.Base(root)) && (action == "install-update" || action == "rollback-update") {
		if !webInstall || action != "install-update" {
			return errors.New("请从原安装目录的启动工具箱.cmd 执行安装或回滚")
		}
		root, err = desktopInstallRoot(exe)
		if err != nil {
			return err
		}
	}
	if !resolveRuntimePaths(exe, "", "").Bundled {
		return errors.New("run this command from the installed Windows bundle")
	}
	marker := filepath.Join(root, activeReleaseFile)
	switch action {
	case "rollback-update":
		if len(args) != 0 {
			return errors.New("rollback-update takes no arguments")
		}
		err := os.Remove(marker)
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	case "install-update":
		flags := flag.NewFlagSet(action, flag.ContinueOnError)
		archive := flags.String("archive", "", "official Windows release ZIP")
		digest := flags.String("sha256", "", "expected release digest")
		checkData := flags.String("check-data", "", "existing data directory to check before switching")
		if err := flags.Parse(args); err != nil {
			return err
		}
		if flags.NArg() != 0 || *archive == "" || len(*digest) != 64 {
			return errors.New("install-update requires -archive and -sha256")
		}
		return prepareWindowsUpdate(*archive, *digest, root, func(stage string) error {
			if err := probeRelease(stage); err != nil {
				return err
			}
			if err := probeReleaseService(stage); err != nil {
				return err
			}
			if *checkData != "" {
				if err := probeReleaseData(stage, *checkData); err != nil {
					return err
				}
			}
			file, err := os.CreateTemp(root, ".release-selection-")
			if err != nil {
				return err
			}
			defer os.Remove(file.Name())
			_, writeErr := fmt.Fprintln(file, filepath.Base(stage))
			syncErr := file.Sync()
			closeErr := file.Close()
			if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
				return err
			}
			if webInstall {
				if _, err := desktopInstallRoot(exe); err != nil {
					return err
				}
			}
			previous, readErr := os.ReadFile(marker)
			if readErr != nil && !errors.Is(readErr, os.ErrNotExist) {
				return readErr
			}
			if name := strings.TrimSpace(string(previous)); slotName.MatchString(name) {
				if err := os.WriteFile(stage+".previous", []byte(name), 0600); err != nil {
					return err
				}
			}
			return os.Rename(file.Name(), marker)
		})
	case "launch":
		selected := exe
		data, err := os.ReadFile(marker)
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		name := strings.TrimSpace(string(data))
		if slotName.MatchString(name) {
			stage := filepath.Join(root, name)
			info, err := os.Lstat(stage)
			if err == nil && info.IsDir() && probeRelease(stage) == nil {
				selected = filepath.Join(stage, "toolbox.exe")
			} else {
				selected = previousDesktopExecutable(stage, exe)
				fmt.Fprintln(os.Stderr, "Selected release unavailable; using fallback release.")
			}
		}
		launchErr := startDesktopRelease(selected, exe, args)
		var exit *exec.ExitError
		if selected == exe || !errors.As(launchErr, &exit) || exit.ExitCode() != upgradeRestoredExit || filepath.Base(filepath.Dir(selected)) != name {
			return launchErr
		}
		current, err := os.ReadFile(marker)
		if err != nil || strings.TrimSpace(string(current)) != name {
			return errors.Join(launchErr, errors.New("upgrade selection changed during rollback"))
		}
		previous := previousDesktopExecutable(filepath.Dir(selected), exe)
		if previous == exe {
			err = os.Remove(marker)
		} else {
			file, createErr := os.CreateTemp(root, ".rollback-selection-")
			if createErr != nil {
				return createErr
			}
			defer os.Remove(file.Name())
			_, writeErr := fmt.Fprintln(file, filepath.Base(filepath.Dir(previous)))
			err = errors.Join(writeErr, file.Sync(), file.Close())
			if err == nil {
				err = os.Rename(file.Name(), marker)
			}
		}
		if err != nil {
			return errors.Join(launchErr, err)
		}
		fmt.Fprintln(os.Stderr, "已恢复升级前数据，正在重新启动原版本。")
		return startDesktopRelease(previous, previous, args)
	}
	return errors.New("unknown desktop release command")
}

func previousDesktopExecutable(stage, original string) string {
	data, err := os.ReadFile(stage + ".previous")
	name := strings.TrimSpace(string(data))
	if err != nil || !slotName.MatchString(name) || name == filepath.Base(stage) {
		return original
	}
	previous := filepath.Join(filepath.Dir(stage), name)
	resolved, err := filepath.EvalSymlinks(previous)
	if err != nil || !strings.EqualFold(resolved, previous) || probeRelease(previous) != nil {
		return original
	}
	return filepath.Join(previous, "toolbox.exe")
}

func startDesktopRelease(selected, original string, args []string) error {
	commandFor := func(path string) *exec.Cmd {
		command := exec.Command(path, args...)
		command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
		return command
	}
	command := commandFor(selected)
	if err := command.Start(); err != nil {
		if selected == original {
			return err
		}
		fmt.Fprintln(os.Stderr, "Selected release could not start; starting original release.")
		if fallbackErr := commandFor(original).Run(); fallbackErr != nil {
			return errors.Join(err, fallbackErr)
		}
		return nil
	}
	return command.Wait()
}
