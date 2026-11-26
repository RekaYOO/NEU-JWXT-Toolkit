package web

import (
	"errors"
	"io/fs"
	"mime"
	"net/http"
	"os"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var hashedAsset = regexp.MustCompile(`\.[0-9a-f]{8,}\.`)
var recoveryPage = regexp.MustCompile(`^auth-recovery/[0-9a-f]{64}\.[0-9a-f]{64}/?$`)

type Static struct{ root *os.Root }

func NewStatic(directory string) (*Static, error) {
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, err
	}
	for _, name := range []string{"index.html", "asset-manifest.json"} {
		if _, err := root.Stat(name); err != nil {
			root.Close()
			return nil, err
		}
	}
	return &Static{root: root}, nil
}

func (handler *Static) Close() error { return handler.root.Close() }

func encodingQuality(header, encoding string) float64 {
	wildcard := 0.0
	for _, value := range strings.Split(strings.ToLower(header), ",") {
		parts := strings.Split(value, ";")
		quality := 1.0
		for _, parameter := range parts[1:] {
			parameter = strings.TrimSpace(parameter)
			if strings.HasPrefix(parameter, "q=") {
				parsed, err := strconv.ParseFloat(strings.TrimPrefix(parameter, "q="), 64)
				if err != nil || parsed < 0 || parsed > 1 {
					quality = 0
				} else {
					quality = parsed
				}
			}
		}
		if strings.TrimSpace(parts[0]) == encoding {
			return quality
		}
		if strings.TrimSpace(parts[0]) == "*" {
			wildcard = quality
		}
	}
	return wildcard
}

func (handler *Static) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.Header().Set("Referrer-Policy", "same-origin")
	name := strings.TrimPrefix(request.URL.Path, "/")
	firstSegment, _, _ := strings.Cut(name, "/")
	if strings.EqualFold(firstSegment, "admin") {
		response.Header().Set("Cache-Control", "no-store")
		http.NotFound(response, request)
		return
	}
	if firstSegment == "api" {
		http.NotFound(response, request)
		return
	}
	if request.Method != http.MethodGet && request.Method != http.MethodHead {
		response.Header().Set("Allow", "GET, HEAD")
		http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	isRecovery := recoveryPage.MatchString(name)
	if isRecovery {
		response.Header().Set("Referrer-Policy", "no-referrer")
		response.Header().Set("X-Robots-Tag", "noindex, nofollow")
	}
	if strings.ContainsAny(name, "\\\x00") || strings.Contains(name, "%") {
		http.NotFound(response, request)
		return
	}
	for _, part := range strings.Split(name, "/") {
		if strings.HasPrefix(part, ".") {
			http.NotFound(response, request)
			return
		}
	}
	allowed := strings.HasPrefix(name, "static/")
	for _, filename := range []string{"index.html", "asset-manifest.json", "manifest.webmanifest", "favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png", "robots.txt"} {
		allowed = allowed || name == filename
	}
	if !allowed {
		if path.Ext(name) != "" && !isRecovery {
			http.NotFound(response, request)
			return
		}
		name = "index.html"
	}
	encoding := ""
	selected := name
	candidates := []string{"br", "gzip"}
	sort.SliceStable(candidates, func(left, right int) bool {
		return encodingQuality(request.Header.Get("Accept-Encoding"), candidates[left]) > encodingQuality(request.Header.Get("Accept-Encoding"), candidates[right])
	})
	for _, candidate := range candidates {
		if encodingQuality(request.Header.Get("Accept-Encoding"), candidate) <= 0 {
			continue
		}
		suffix := ".br"
		if candidate == "gzip" {
			suffix = ".gz"
		}
		if info, err := handler.root.Stat(name + suffix); err == nil && !info.IsDir() {
			selected, encoding = name+suffix, candidate
			break
		}
	}
	file, err := handler.root.Open(selected)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			http.NotFound(response, request)
		} else {
			http.Error(response, "asset unavailable", http.StatusNotFound)
		}
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || info.IsDir() {
		http.NotFound(response, request)
		return
	}
	response.Header().Set("Cache-Control", "no-cache")
	if isRecovery {
		response.Header().Set("Cache-Control", "no-store")
	}
	if hashedAsset.MatchString(path.Base(name)) {
		response.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	}
	response.Header().Set("Vary", "Accept-Encoding")
	if encoding != "" {
		response.Header().Set("Content-Encoding", encoding)
	}
	if contentType := mime.TypeByExtension(path.Ext(name)); contentType != "" {
		response.Header().Set("Content-Type", contentType)
	}
	http.ServeContent(response, request, name, info.ModTime(), file)
}
