package application

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

const releaseRepository = "RekaYOO/NEU-JWXT-Toolkit"
const releasePage = "https://github.com/" + releaseRepository + "/releases/latest"
const releaseAPI = "https://api.github.com/repos/" + releaseRepository + "/releases/latest"

var releaseVersion = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$`)
var releaseDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)
var releaseNumeric = regexp.MustCompile(`^[0-9]+$`)

type runtimeReleaseAsset struct {
	Name   string `json:"name"`
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
	URL    string `json:"-"`
}

type runtimeRelease struct {
	Schema  int                            `json:"schema_version"`
	Version string                         `json:"version"`
	Runtime string                         `json:"runtime"`
	Assets  map[string]runtimeReleaseAsset `json:"assets"`
}

type runtimeUpdateChecker struct {
	install     func(string, string) error
	installBusy bool
	prepare     func(string, string, string) (string, error)
	jobSaved    []byte
	directory   string
	job         *runtimeUpdateJob
	mutex       sync.Mutex
	client      *http.Client
	latest      *runtimeRelease
	checked     time.Time
	failure     error
}

func compareReleaseVersions(left, right string) (int, error) {
	first, second := releaseVersion.FindStringSubmatch(left), releaseVersion.FindStringSubmatch(right)
	if first == nil || second == nil || len(left) > 128 || len(right) > 128 {
		return 0, errors.New("发行版本格式无效")
	}
	for _, version := range [][]string{first, second} {
		for _, part := range strings.Split(version[4], ".") {
			if len(part) > 1 && part[0] == '0' {
				if _, numeric := new(big.Int).SetString(part, 10); numeric {
					return 0, errors.New("预发行版本格式无效")
				}
			}
		}
	}
	for index := 1; index <= 3; index++ {
		firstNumber, _ := new(big.Int).SetString(first[index], 10)
		secondNumber, _ := new(big.Int).SetString(second[index], 10)
		if compared := firstNumber.Cmp(secondNumber); compared != 0 {
			return compared, nil
		}
	}
	if first[4] == second[4] {
		return 0, nil
	}
	if first[4] == "" {
		return 1, nil
	}
	if second[4] == "" {
		return -1, nil
	}
	firstParts, secondParts := strings.Split(first[4], "."), strings.Split(second[4], ".")
	for index := 0; index < min(len(firstParts), len(secondParts)); index++ {
		firstNumber, _ := new(big.Int).SetString(firstParts[index], 10)
		secondNumber, _ := new(big.Int).SetString(secondParts[index], 10)
		firstNumeric, secondNumeric := releaseNumeric.MatchString(firstParts[index]), releaseNumeric.MatchString(secondParts[index])
		if firstNumeric && secondNumeric {
			if compared := firstNumber.Cmp(secondNumber); compared != 0 {
				return compared, nil
			}
		} else if firstNumeric {
			return -1, nil
		} else if secondNumeric {
			return 1, nil
		} else if compared := strings.Compare(firstParts[index], secondParts[index]); compared != 0 {
			return compared, nil
		}
	}
	if len(firstParts) < len(secondParts) {
		return -1, nil
	}
	return 1, nil
}

func allowedReleaseRedirect(target *url.URL) bool {
	return target.Scheme == "https" && target.User == nil && target.Port() == "" && (target.Hostname() == "github.com" || target.Hostname() == "api.github.com" || target.Hostname() == "release-assets.githubusercontent.com" || target.Hostname() == "objects.githubusercontent.com")
}

func (checker *runtimeUpdateChecker) readJSON(ctx context.Context, target string, value any) error {
	parsed, err := url.Parse(target)
	if err != nil || !allowedReleaseRedirect(parsed) {
		return errors.New("发行地址不受信任")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return errors.New("发行请求无效")
	}
	request.Header.Set("User-Agent", "NEU-JWXT-Toolkit-Go-update-checker")
	request.Header.Set("Accept", "application/vnd.github+json")
	response, err := checker.client.Do(request)
	if err != nil {
		return errors.New("无法连接 GitHub Release")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return errors.New("GitHub Release 暂不可用")
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024+1))
	if err != nil || len(payload) > 4*1024*1024 || json.Unmarshal(payload, value) != nil {
		return errors.New("发行元数据无效或过大")
	}
	return nil
}

func (checker *runtimeUpdateChecker) fetch(ctx context.Context) (*runtimeRelease, error) {
	var release struct {
		Tag        string `json:"tag_name"`
		Draft      bool   `json:"draft"`
		Prerelease bool   `json:"prerelease"`
		Assets     []struct {
			Name string `json:"name"`
			URL  string `json:"browser_download_url"`
			Size int64  `json:"size"`
		} `json:"assets"`
	}
	if err := checker.readJSON(ctx, releaseAPI, &release); err != nil {
		return nil, err
	}
	version := strings.TrimPrefix(release.Tag, "v")
	if _, err := compareReleaseVersions(version, version); err != nil || release.Tag != "v"+version || release.Draft || release.Prerelease {
		return nil, errors.New("正式发行版本无效")
	}
	prefix := "https://github.com/" + releaseRepository + "/releases/download/" + release.Tag + "/"
	assets := map[string]runtimeReleaseAsset{}
	for _, asset := range release.Assets {
		if strings.ContainsAny(asset.Name, "/\\?#%") || asset.Name == "" || asset.URL != prefix+asset.Name || asset.Size < 1 || asset.Size > 1024*1024*1024 {
			return nil, errors.New("发行资产地址或大小无效")
		}
		if _, exists := assets[asset.Name]; exists {
			return nil, errors.New("发行资产重复")
		}
		assets[asset.Name] = runtimeReleaseAsset{Name: asset.Name, URL: asset.URL, Size: asset.Size}
	}
	metadata, exists := assets["go-release-manifest.json"]
	if !exists {
		return nil, errors.New("当前发行尚未提供 Go/Web 版本清单")
	}
	var manifest runtimeRelease
	if err := checker.readJSON(ctx, metadata.URL, &manifest); err != nil {
		return nil, err
	}
	if manifest.Schema != 1 || manifest.Runtime != "go-web" || manifest.Version != version {
		return nil, errors.New("Go/Web 版本清单与发行不一致")
	}
	verified := make(map[string]runtimeReleaseAsset, 2)
	for _, platform := range []string{"windows", "linux"} {
		key := platform + "-amd64"
		asset := manifest.Assets[key]
		suffix := ".zip"
		if platform == "linux" {
			suffix = ".tar.gz"
		}
		expected := "NEU-JWXT-Toolkit-" + version + "-" + platform + "-amd64-go" + suffix
		remote, found := assets[expected]
		if !found || asset.Name != expected || asset.Size != remote.Size || !releaseDigest.MatchString(asset.SHA256) {
			return nil, errors.New("Go/Web 资产校验清单无效")
		}
		asset.URL = remote.URL
		verified[key] = asset
	}
	manifest.Assets = verified
	return &manifest, nil
}

func (api *API) runtimeUpdate(response http.ResponseWriter, request *http.Request) {
	checker := &api.releaseChecker
	checker.mutex.Lock()
	defer checker.mutex.Unlock()
	if checker.client == nil {
		checker.client = &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(next *http.Request, via []*http.Request) error {
			if len(via) >= 5 || !allowedReleaseRedirect(next.URL) {
				return errors.New("untrusted release redirect")
			}
			return nil
		}}
	}
	force := request.URL.Query().Get("force") == "true"
	if checker.checked.IsZero() || time.Since(checker.checked) >= 10*time.Minute || force && time.Since(checker.checked) >= 30*time.Second {
		ctx, cancel := context.WithTimeout(request.Context(), 20*time.Second)
		checker.latest, checker.failure = checker.fetch(ctx)
		cancel()
		checker.checked = time.Now()
	}
	profile := "desktop"
	if api.accounts.mode != "local-single" {
		profile = "server"
	}
	result := map[string]any{"current_version": api.version, "profile": profile, "capability": "none", "latest_version": nil, "android_version_code": nil, "update_available": false, "release_url": releasePage, "asset": nil, "error": nil, "job": nil}
	if api.mayManageUpdates(request) && checker.directory != "" {
		result["can_install_on_restart"] = checker.install != nil && api.accounts.mode == "local-single"
		result["capability"] = "go-manual"
		if checker.job != nil {
			result["job"] = *checker.job
		}
	}
	if checker.failure != nil {
		result["error"] = checker.failure.Error()
	} else if checker.latest != nil {
		comparison, err := compareReleaseVersions(checker.latest.Version, api.version)
		if err != nil {
			result["error"] = err.Error()
		} else {
			result["latest_version"], result["update_available"] = checker.latest.Version, comparison > 0
			result["release_url"] = "https://github.com/" + releaseRepository + "/releases/tag/v" + checker.latest.Version
			if asset, exists := checker.latest.Assets[runtime.GOOS+"-"+runtime.GOARCH]; exists {
				result["asset"] = asset
			}
		}
	}
	writeJSON(response, 200, result)
}
