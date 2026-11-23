package application

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) registerLogs(router *http.ServeMux) {
	for _, route := range []string{"GET /api/logs/summary", "GET /api/logs/files", "GET /api/logs/content", "GET /api/logs/tail", "GET /api/logs/search", "GET /api/logs/download/{category}/{date}", "DELETE /api/logs/cleanup"} {
		router.HandleFunc(route, api.logs)
	}
}

func logInteger(request *http.Request, key string, fallback, minimum, maximum int) (int, bool) {
	text := request.URL.Query().Get(key)
	if text == "" {
		return fallback, true
	}
	value, err := strconv.Atoi(text)
	return value, err == nil && value >= minimum && value <= maximum
}

func (api *API) logs(response http.ResponseWriter, request *http.Request) {
	api.serveLogs(response, request, api.database.WriteDiagnosticFile)
}

func (api *API) serveLogs(response http.ResponseWriter, request *http.Request, writeFile func(context.Context, store.DiagnosticQuery, io.Writer) error) {
	query := store.DiagnosticQuery{All: api.siteAuthenticated(request, "admin") || api.accounts.mode == "local-single"}
	var generation int64
	if !query.All {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		query.Account = session.Account
		generation = session.Generation
	}
	action := strings.TrimPrefix(request.URL.Path, "/api/logs/")
	query.Category, query.Date = request.URL.Query().Get("category"), request.URL.Query().Get("date")
	if strings.HasPrefix(action, "download/") {
		action = "download"
		query.Category, query.Date = request.PathValue("category"), request.PathValue("date")
	}
	days, daysOK := logInteger(request, "days", 7, 1, 30)
	limit, limitOK := logInteger(request, "limit", 100, 1, 1000)
	offset, offsetOK := logInteger(request, "offset", 0, 0, 200000)
	lines, linesOK := logInteger(request, "lines", 100, 1, 500)
	keep, keepOK := logInteger(request, "keep_days", 30, 7, 365)
	validCategory := query.Category == "" || query.Category == "system" || query.Category == "access" || query.Category == "error" || query.Category == "login" || query.Category == "sync"
	_, dateErr := time.Parse("2006-01-02", query.Date)
	requiresDate := action == "content" || action == "tail" || action == "download"
	query.Level, query.Search = request.URL.Query().Get("level"), request.URL.Query().Get("search")
	validLevel := query.Level == "" || query.Level == "DEBUG" || query.Level == "INFO" || query.Level == "WARNING" || query.Level == "ERROR" || query.Level == "CRITICAL"
	if !daysOK || !limitOK || !offsetOK || !linesOK || !keepOK || !validCategory || !validLevel || (requiresDate && (query.Category == "" || dateErr != nil)) || len(query.Search) > 500 {
		writeJSON(response, 422, map[string]string{"detail": "日志查询条件无效"})
		return
	}
	query.Limit, query.Offset = limit, offset
	if !requiresDate {
		query.Since = time.Now().UTC().AddDate(0, 0, 1-days).Format("2006-01-02")
	}
	if action == "cleanup" {
		count, err := api.database.CleanupDiagnostics(request.Context(), query.Account, query.All, time.Now().UTC().AddDate(0, 0, 1-keep).Format("2006-01-02"))
		if err != nil {
			api.documentError(response, err)
			return
		}
		writeJSON(response, 200, map[string]any{"deleted_files": count, "keep_days": keep})
		return
	}
	if action == "download" {
		file, err := os.CreateTemp("", "neu-toolbox-log-*.tmp")
		if err != nil {
			api.documentError(response, err)
			return
		}
		defer os.Remove(file.Name())
		defer file.Close()
		writer := bufio.NewWriter(file)
		err = writeFile(request.Context(), query, writer)
		if errors.Is(err, store.ErrMissing) {
			writeJSON(response, 404, map[string]string{"detail": "日志文件不存在"})
			return
		}
		if err == nil {
			err = writer.Flush()
		}
		var size int64
		if err == nil {
			size, err = file.Seek(0, io.SeekCurrent)
		}
		if err == nil {
			_, err = file.Seek(0, io.SeekStart)
		}
		if err != nil {
			api.documentError(response, err)
			return
		}
		if api.gatewayRequired && !api.siteAuthenticated(request, "access") {
			writeJSON(response, 401, map[string]string{"detail": "需要重新验证网站访问密码", "error_code": "ACCESS_REQUIRED"})
			return
		}
		if api.accounts.mode != "local-single" {
			if query.All {
				if !api.requireAdmin(response, request) {
					return
				}
			} else if current, err := api.session(request); err != nil || current.Account != query.Account || current.Generation != generation {
				api.resourceError(response, store.ErrAccess)
				return
			}
		}
		response.Header().Set("Content-Type", "text/plain; charset=utf-8")
		response.Header().Set("Cache-Control", "no-store")
		response.Header().Set("Content-Length", strconv.FormatInt(size, 10))
		response.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s_%s.log"`, query.Category, query.Date))
		io.Copy(response, file)
		return
	}
	if action == "files" || action == "summary" {
		files, err := api.database.DiagnosticFiles(request.Context(), query)
		if err != nil {
			api.documentError(response, err)
			return
		}
		if action == "files" {
			writeJSON(response, 200, files)
			return
		}
		if action == "summary" {
			categories := map[string]any{}
			var size int64
			for _, category := range []string{"system", "access", "error", "login", "sync"} {
				group := []store.DiagnosticFile{}
				var groupSize int64
				for _, file := range files {
					if file.Category == category {
						group = append(group, file)
						groupSize += file.SizeBytes
					}
				}
				count := len(group)
				if len(group) > 5 {
					group = group[:5]
				}
				categories[category] = map[string]any{"file_count": count, "total_size_bytes": groupSize, "files": group}
				size += groupSize
			}
			writeJSON(response, 200, map[string]any{"period_days": days, "categories": categories, "total_files": len(files), "total_size_bytes": size, "total_size_mb": float64(size) / (1024 * 1024)})
			return
		}
	}
	if action == "tail" {
		query.Limit, query.Offset, query.Newest = lines, 0, true
	}
	if action == "search" {
		query.Search = request.URL.Query().Get("keyword")
		query.Newest = true
		if strings.TrimSpace(query.Search) == "" || len(query.Search) > 500 || limit > 500 {
			writeJSON(response, 422, map[string]string{"detail": "搜索条件无效"})
			return
		}
	}
	entries, err := api.database.DiagnosticEntries(request.Context(), query)
	if err != nil {
		api.documentError(response, err)
		return
	}
	result := map[string]any{"category": query.Category, "date": query.Date, "total_returned": len(entries), "entries": entries, "lines": len(entries)}
	if action == "search" {
		result = map[string]any{"keyword": query.Search, "total": len(entries), "results": entries}
	}
	writeJSON(response, 200, result)
}

type diagnosticResponse struct {
	http.ResponseWriter
	status int
}

func (response *diagnosticResponse) WriteHeader(status int) {
	if response.status == 0 {
		response.status = status
		response.ResponseWriter.WriteHeader(status)
	}
}
func (response *diagnosticResponse) Write(payload []byte) (int, error) {
	if response.status == 0 {
		response.WriteHeader(http.StatusOK)
	}
	return response.ResponseWriter.Write(payload)
}
func (response *diagnosticResponse) Unwrap() http.ResponseWriter { return response.ResponseWriter }

func (api *API) observeRequest(response http.ResponseWriter, request *http.Request, next http.Handler) {
	if !strings.HasPrefix(request.URL.Path, "/api/") || strings.HasPrefix(request.URL.Path, "/api/logs/") {
		next.ServeHTTP(response, request)
		return
	}
	account := ""
	if session, err := api.session(request); err == nil {
		account = session.Account
	}
	started := time.Now()
	record := &diagnosticResponse{ResponseWriter: response}
	next.ServeHTTP(record, request)
	if account == "" {
		for _, cookie := range (&http.Response{Header: response.Header()}).Cookies() {
			if cookie.Name == sessionCookie {
				copyRequest := request.Clone(request.Context())
				copyRequest.Header = request.Header.Clone()
				copyRequest.Header.Del("Cookie")
				copyRequest.AddCookie(cookie)
				if session, err := api.session(copyRequest); err == nil {
					account = session.Account
				}
			}
		}
	}
	pattern := request.Pattern
	if pattern == "" {
		pattern = "unmatched"
	}
	status := record.status
	if status == 0 {
		status = 200
	}
	category, level := "access", "INFO"
	if status >= 500 {
		category, level = "error", "ERROR"
	} else if status >= 400 {
		level = "WARNING"
	}
	if strings.Contains(pattern, "/login") || strings.Contains(pattern, "/logout") || strings.Contains(pattern, "/auth") || strings.Contains(pattern, "/webvpn") {
		category = "login"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	api.database.AppendDiagnostic(ctx, account, store.DiagnosticEntry{Category: category, Level: level, EventType: "http_access", EventTitle: "HTTP 请求", Summary: fmt.Sprintf("%s · %d", pattern, status), Details: map[string]any{"route": pattern, "status_code": status, "response_time_ms": time.Since(started).Milliseconds()}})
}
