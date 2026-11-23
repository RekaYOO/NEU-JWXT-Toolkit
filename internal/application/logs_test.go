package application

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestRemoteDiagnosticReadIsolation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
		if err := api.database.AppendDiagnostic(ctx, account, store.DiagnosticEntry{Category: "system", Level: "INFO", Summary: "fixture-" + account}); err != nil {
			t.Fatal(err)
		}
	}
	cookie := loginCookie(t, handler, "student-a")
	day := time.Now().UTC().Format("2006-01-02")
	for _, path := range []string{"content?category=system&date=" + day, "tail?category=system&date=" + day, "search?keyword=fixture", "download/system/" + day} {
		endpoint := "/api/logs/" + path
		result := apiRequest(handler, "GET", endpoint, "", cookie)
		if result.Code != 200 || !strings.Contains(result.Body.String(), "fixture-student-a") || strings.Contains(result.Body.String(), "fixture-student-b") {
			t.Fatalf("%s: %d %s", path, result.Code, result.Body.String())
		}
		if anonymous := apiRequest(handler, "GET", endpoint, "", nil); anonymous.Code != 401 {
			t.Fatalf("anonymous %s: %d", path, anonymous.Code)
		}
	}
}

func TestDailyLogDownloadBeyond16MiB(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	content := strings.Repeat("s", 15000)
	for index := 0; index < 1120; index++ {
		if err := api.database.AppendDiagnostic(context.Background(), "fixture", store.DiagnosticEntry{Category: "system", Level: "INFO", Message: content, Summary: strconv.Itoa(index)}); err != nil {
			t.Fatal(err)
		}
	}
	result := apiRequest(handler, "GET", "/api/logs/download/system/"+time.Now().UTC().Format("2006-01-02"), "", nil)
	if result.Code != 200 || result.Body.Len() <= 16<<20 || result.Header().Get("Content-Length") != strconv.Itoa(result.Body.Len()) {
		t.Fatal(result.Code, result.Body.Len())
	}
	lines := bytes.Split(bytes.TrimSuffix(result.Body.Bytes(), []byte("\n")), []byte("\n"))
	if len(lines) != 1120 || !bytes.Contains(lines[0], []byte(`"summary":"0"`)) || !bytes.Contains(lines[1119], []byte(`"summary":"1119"`)) {
		t.Fatal("incomplete or reordered download", len(lines))
	}
	if missing := apiRequest(handler, "GET", "/api/logs/download/system/2001-01-01", "", nil); missing.Code != 404 {
		t.Fatal(missing.Code)
	}
}

func TestLogDownloadRevokedAdminCannotFallbackToUser(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
		if err := api.database.AppendDiagnostic(ctx, account, store.DiagnosticEntry{Category: "system", Summary: "private-" + account}); err != nil {
			t.Fatal(err)
		}
	}
	user := loginCookie(t, handler, "student-a")
	const password = "synthetic-password"
	if err := api.database.SetSitePassword(ctx, "admin", password, false); err != nil {
		t.Fatal(err)
	}
	token, err := api.database.CreateSiteSession(ctx, "admin", password, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("GET", "http://localhost/api/logs/download/system/"+time.Now().UTC().Format("2006-01-02"), nil)
	request.AddCookie(user)
	request.AddCookie(&http.Cookie{Name: siteCookie("admin"), Value: token})
	response := httptest.NewRecorder()
	router := http.NewServeMux()
	generated := false
	router.HandleFunc("GET /api/logs/download/{category}/{date}", func(w http.ResponseWriter, r *http.Request) {
		api.serveLogs(w, r, func(ctx context.Context, query store.DiagnosticQuery, out io.Writer) error {
			var content bytes.Buffer
			if err := api.database.WriteDiagnosticFile(ctx, query, io.MultiWriter(out, &content)); err != nil {
				return err
			}
			generated = strings.Contains(content.String(), "private-student-a") && strings.Contains(content.String(), "private-student-b")
			return api.database.RevokeSiteSession(ctx, "admin", token)
		})
	})
	api.Boundary(router).ServeHTTP(response, request)
	if !generated || response.Code != 401 || !strings.Contains(response.Body.String(), "ADMIN_REQUIRED") || strings.Contains(response.Body.String(), "private-student") || response.Header().Get("Content-Disposition") != "" {
		t.Fatal("revoked admin download not rejected", response.Code)
	}
	if _, err := api.database.Session(ctx, user.Value); err != nil {
		t.Fatal("ordinary session unexpectedly revoked", err)
	}
}
