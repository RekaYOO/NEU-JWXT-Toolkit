//go:build ignore

package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func password() string {
	var secret [24]byte
	_, err := rand.Read(secret[:])
	check(err)
	return hex.EncodeToString(secret[:])
}

func main() {
	executable, err := filepath.Abs(os.Args[1])
	check(err)
	directory, err := os.MkdirTemp("", "toolbox-https-")
	check(err)
	defer os.RemoveAll(directory)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	check(err)
	address := listener.Addr().String()
	check(listener.Close())
	upstream, err := url.Parse("http://" + address)
	check(err)
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		request.Header.Set("X-Forwarded-Proto", "https")
		proxy.ServeHTTP(writer, request)
	}))
	defer server.Close()
	access, admin := password(), password()
	command := exec.Command(executable, "--mode", "remote-multi", "--listen", address)
	command.Dir = directory
	command.Env = append(os.Environ(), "NEU_JWXT_DATA_DIR="+directory, "NEU_JWXT_PUBLIC_ORIGIN="+server.URL, "NEU_JWXT_ACCESS_PASSWORD="+access, "NEU_JWXT_ADMIN_PASSWORD="+admin)
	check(command.Start())
	defer func() { command.Process.Kill(); command.Wait() }()
	client := server.Client()
	client.Timeout = 3 * time.Second
	client.Jar, err = cookiejar.New(nil)
	check(err)
	deadline := time.Now().Add(15 * time.Second)
	for {
		response, err := client.Get(server.URL + "/api/health")
		if err == nil {
			response.Body.Close()
			if response.StatusCode == 200 {
				break
			}
		}
		if time.Now().After(deadline) {
			panic("HTTPS health check timed out")
		}
		time.Sleep(100 * time.Millisecond)
	}
	call := func(path, secret string, status int) map[string]any {
		var response *http.Response
		if secret == "" {
			response, err = client.Get(server.URL + path)
		} else {
			payload, err := json.Marshal(map[string]string{"password": secret})
			check(err)
			request, err := http.NewRequest("POST", server.URL+path, bytes.NewReader(payload))
			check(err)
			request.Header.Set("Origin", server.URL)
			request.Header.Set("Content-Type", "application/json")
			response, err = client.Do(request)
			check(err)
		}
		check(err)
		defer response.Body.Close()
		if response.StatusCode != status {
			panic(fmt.Sprintf("%s: %d", path, response.StatusCode))
		}
		payload, err := io.ReadAll(response.Body)
		check(err)
		var result map[string]any
		check(json.Unmarshal(payload, &result))
		return result
	}
	if call("/api/health", "", 200)["mobile_api_version"] != float64(1) {
		panic("unexpected mobile API version")
	}
	if call("/api/admin/accounts", "", 401)["error_code"] != "ACCESS_REQUIRED" {
		panic("anonymous boundary")
	}
	call("/api/access/login", access, 200)
	if call("/api/admin/accounts", "", 401)["error_code"] != "ADMIN_REQUIRED" {
		panic("admin boundary")
	}
	call("/api/admin/login", admin, 200)
	call("/api/admin/accounts", "", 200)
	fmt.Println("HTTPS: mobile health, anonymous denial, access login, admin denial and admin login passed")
}
