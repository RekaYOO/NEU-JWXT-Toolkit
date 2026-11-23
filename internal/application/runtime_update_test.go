package application

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

type releaseTransport func(*http.Request) (*http.Response, error)

func (transport releaseTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return transport(request)
}

func TestReleaseVersionOrdering(t *testing.T) {
	for _, pair := range [][2]string{{"2.10.0", "2.9.9"}, {"2.0.0", "2.0.0-rc.1"}, {"2.0.0-rc.10", "2.0.0-rc.2"}, {"2.0.0-beta", "2.0.0-1"}, {"2.0.0-rc.1.1", "2.0.0-rc.1"}} {
		compared, err := compareReleaseVersions(pair[0], pair[1])
		if err != nil || compared <= 0 {
			t.Fatal(pair, compared, err)
		}
	}
	if compared, err := compareReleaseVersions("2.0.0+build.1", "2.0.0+build.2"); err != nil || compared != 0 {
		t.Fatal(compared, err)
	}
	for _, invalid := range []string{"2.0", "02.0.0", "2.0.0-01", "2.0.0-", "2.0.0/evil"} {
		if _, err := compareReleaseVersions(invalid, "2.0.0"); err == nil {
			t.Fatal(invalid)
		}
	}
}

func TestRuntimeReleaseManifestValidationAndCachedEndpoint(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	api.version = "2.2.4"
	version := "9.0.0"
	prefix := "https://github.com/" + releaseRepository + "/releases/download/v" + version + "/"
	manifest := runtimeRelease{Schema: 1, Version: version, Runtime: "go-web", Assets: map[string]runtimeReleaseAsset{}}
	assets := []map[string]any{{"name": "go-release-manifest.json", "browser_download_url": prefix + "go-release-manifest.json", "size": 100}}
	for _, platform := range []string{"windows", "linux"} {
		suffix := ".zip"
		if platform == "linux" {
			suffix = ".tar.gz"
		}
		name := "NEU-JWXT-Toolkit-" + version + "-" + platform + "-amd64-go" + suffix
		manifest.Assets[platform+"-amd64"] = runtimeReleaseAsset{Name: name, Size: 123, SHA256: strings.Repeat("a", 64)}
		assets = append(assets, map[string]any{"name": name, "browser_download_url": prefix + name, "size": 123})
	}
	requests := 0
	api.releaseChecker.client = &http.Client{Transport: releaseTransport(func(request *http.Request) (*http.Response, error) {
		requests++
		var value any = manifest
		if request.URL.String() == releaseAPI {
			value = map[string]any{"tag_name": "v" + version, "assets": assets}
		} else if request.URL.String() != prefix+"go-release-manifest.json" {
			t.Fatal("unexpected URL", request.URL)
		}
		payload, _ := json.Marshal(value)
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(payload))), Header: http.Header{}}, nil
	})}
	for index := 0; index < 2; index++ {
		response := apiRequest(handler, "GET", "/api/runtime/update?force=true", "", nil)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"latest_version":"9.0.0"`) || !strings.Contains(response.Body.String(), `"update_available":true`) {
			t.Fatal(response.Code, response.Body.String())
		}
	}
	if requests != 2 {
		t.Fatal("repeated requests bypassed cache", requests)
	}
	manifest.Runtime = "python"
	if _, err := api.releaseChecker.fetch(context.Background()); err == nil {
		t.Fatal("legacy runtime accepted")
	}
	manifest.Runtime = "go-web"
	manifest.Assets["linux-arm64"] = runtimeReleaseAsset{Name: "unverified.tar.gz"}
	checked, err := api.releaseChecker.fetch(context.Background())
	if err != nil || len(checked.Assets) != 2 || checked.Assets["linux-arm64"].Name != "" {
		t.Fatal("unexpected platform selection", checked, err)
	}
	assets[1]["browser_download_url"] = "https://github.com/attacker/other/releases/download/v9.0.0/payload.zip"
	if _, err := api.releaseChecker.fetch(context.Background()); err == nil {
		t.Fatal("foreign repository accepted")
	}
}

func TestReleaseRedirectTrust(t *testing.T) {
	for _, target := range []string{"http://github.com/file", "https://github.com.evil.test/file", "https://user@github.com/file", "https://github.com:444/file", "https://127.0.0.1/file"} {
		parsed, _ := url.Parse(target)
		if allowedReleaseRedirect(parsed) {
			t.Fatal(target)
		}
	}
	parsed, _ := url.Parse("https://release-assets.githubusercontent.com/file")
	if !allowedReleaseRedirect(parsed) {
		t.Fatal("official asset redirect rejected")
	}
}
