package main

import (
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"time"
)

func saveDesktopInstance(directory, endpoint string) error {
	payload, _ := json.Marshal(map[string]string{"url": endpoint})
	file, err := os.CreateTemp(directory, "desktop-instance-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	_, writeErr := file.Write(payload)
	syncErr := file.Sync()
	closeErr := file.Close()
	if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(directory, "desktop-instance.json"))
}

func existingDesktop(directory string) (string, error) {
	file, err := os.Open(filepath.Join(directory, "desktop-instance.json"))
	if err != nil {
		return "", err
	}
	defer file.Close()
	var metadata struct {
		URL string `json:"url"`
	}
	if json.NewDecoder(io.LimitReader(file, 4096)).Decode(&metadata) != nil {
		return "", errors.New("invalid desktop metadata")
	}
	parsed, err := url.Parse(metadata.URL)
	if err != nil || parsed.Scheme != "http" || parsed.User != nil || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.ForceQuery || parsed.Port() == "" {
		return "", errors.New("invalid desktop URL")
	}
	address := net.ParseIP(parsed.Hostname())
	if address == nil || !address.IsLoopback() {
		return "", errors.New("desktop URL is not loopback")
	}
	client := http.Client{Timeout: time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("unexpected desktop redirect") }}
	response, err := client.Get(metadata.URL + "/api/health")
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	var health struct {
		Backend string `json:"backend"`
		Profile string `json:"profile"`
	}
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&health) != nil || health.Backend != "go" || health.Profile != "desktop" {
		return "", errors.New("desktop service not available")
	}
	return metadata.URL, nil
}
