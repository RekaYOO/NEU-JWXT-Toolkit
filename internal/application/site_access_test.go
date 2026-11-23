package application

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func requestWithSiteCookies(handler http.Handler, method, path, body string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, "http://localhost"+path, strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	for _, cookie := range cookies {
		if cookie != nil {
			request.AddCookie(cookie)
		}
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func TestInitialSitePasswordsAreAtomic(t *testing.T) {
	api, _ := testApplication(t, "remote-multi")
	ctx := context.Background()
	if err := api.ConfigureRemoteAccess(ctx, "unused-first-password", "short"); err == nil {
		t.Fatal("invalid administrator password accepted")
	}
	for _, purpose := range []string{"access", "admin"} {
		if configured, err := api.database.SitePasswordConfigured(ctx, purpose); err != nil || configured {
			t.Fatalf("partial %s credential: %v", purpose, err)
		}
	}
	if err := api.ConfigureRemoteAccess(ctx, "corrected-access-password", "corrected-admin-password"); err != nil {
		t.Fatal(err)
	}
	if _, err := api.database.CreateSiteSession(ctx, "access", "corrected-access-password", time.Hour); err != nil {
		t.Fatal(err)
	}
}

func setupSiteAdmin(t *testing.T, mode string) (*API, http.Handler, *http.Cookie, *http.Cookie) {
	t.Helper()
	api, handler := testApplication(t, mode)
	api.gatewayRequired, api.SecureCookies = true, true
	api.accounts.mutex.Lock()
	api.accounts.mailTick, api.accounts.automationTick = nil, nil
	api.accounts.mutex.Unlock()
	if err := api.ConfigureRemoteAccess(context.Background(), "test-access-password", "test-admin-password"); err != nil {
		t.Fatal(err)
	}
	response := requestWithSiteCookies(handler, "POST", "/api/access/login", `{"password":"test-access-password"}`)
	if response.Code != 200 {
		t.Fatal(response)
	}
	access := responseCookie(t, response.Result(), siteCookie("access"))
	if !access.Secure || !access.HttpOnly {
		t.Fatal("unsafe access cookie")
	}
	response = requestWithSiteCookies(handler, "POST", "/api/admin/login", `{"password":"test-admin-password"}`, access)
	if response.Code != 200 {
		t.Fatal(response)
	}
	admin := responseCookie(t, response.Result(), siteCookie("admin"))
	return api, handler, access, admin
}

func TestRemoteGatewayFailsClosedAndSeparatesAdministrator(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.gatewayRequired = true
	for _, path := range []string{"/api/status", "/api/client/bootstrap", "/api/documents/gpa/default", "/api/admin/accounts"} {
		if response := requestWithSiteCookies(handler, "GET", path, ""); response.Code != 401 || !strings.Contains(response.Body.String(), "ACCESS_REQUIRED") {
			t.Fatal(response)
		}
	}
	if response := requestWithSiteCookies(handler, "GET", "/api/health", ""); response.Code != 200 {
		t.Fatal(response)
	}
	response := requestWithSiteCookies(handler, "GET", "/api/access/status", "")
	if !strings.Contains(response.Body.String(), `"configured":false`) || !strings.Contains(response.Body.String(), `"authenticated":false`) {
		t.Fatal(response)
	}
	if err := api.ConfigureRemoteAccess(context.Background(), "test-access-password", "test-admin-password"); err != nil {
		t.Fatal(err)
	}
	response = requestWithSiteCookies(handler, "POST", "/api/access/login", `{"password":"test-access-password"}`)
	access := responseCookie(t, response.Result(), siteCookie("access"))
	if response := requestWithSiteCookies(handler, "GET", "/api/admin/accounts", "", access); response.Code != 401 || !strings.Contains(response.Body.String(), "ADMIN_REQUIRED") {
		t.Fatal(response)
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/login", `{"password":"test-access-password"}`, access); response.Code != 401 {
		t.Fatal("site password granted administration", response)
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/access/logout", `{}`, access); response.Code != 200 {
		t.Fatal(response)
	}
	if response := requestWithSiteCookies(handler, "GET", "/api/status", "", access); response.Code != 401 {
		t.Fatal("logged-out gateway token accepted", response)
	}
}

func TestRemoteSiteCookiesOverTLS(t *testing.T) {
	_, handler, _, _ := setupSiteAdmin(t, "remote-multi")
	secure := httptest.NewTLSServer(handler)
	defer secure.Close()
	plain := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if len(request.Cookies()) != 0 {
			response.WriteHeader(http.StatusForbidden)
			return
		}
		response.WriteHeader(http.StatusNoContent)
	}))
	defer plain.Close()
	client := secure.Client()
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	client.Jar = jar
	transport := client.Transport.(*http.Transport).Clone()
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		_, port, err := net.SplitHostPort(address)
		if err != nil {
			return nil, err
		}
		return (&net.Dialer{}).DialContext(ctx, network, net.JoinHostPort("127.0.0.1", port))
	}
	client.Transport = transport
	defer transport.CloseIdleConnections()
	call := func(method, target, body string, expected int) {
		t.Helper()
		request, err := http.NewRequest(method, strings.Replace(target, "127.0.0.1", "example.com", 1), strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		request.Header.Set("Content-Type", "application/json")
		result, err := client.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		defer result.Body.Close()
		if result.StatusCode != expected {
			t.Fatalf("%s: status %d; expected %d", request.URL.Path, result.StatusCode, expected)
		}
	}
	call("POST", secure.URL+"/api/access/login", `{"password":"test-access-password"}`, 200)
	call("GET", secure.URL+"/api/admin/accounts", "", 401)
	call("POST", secure.URL+"/api/admin/login", `{"password":"test-admin-password"}`, 200)
	call("GET", secure.URL+"/api/admin/accounts", "", 200)
	call("GET", plain.URL+"/api/admin/accounts", "", 204)
	call("POST", secure.URL+"/api/access/logout", `{}`, 200)
	call("GET", secure.URL+"/api/admin/accounts", "", 401)
	call("POST", secure.URL+"/api/access/login", `{"password":"test-access-password"}`, 200)
	call("GET", secure.URL+"/api/admin/accounts", "", 401)
}

func TestAdminTwoAccountLifecycleKeepsDataAndStopsTasks(t *testing.T) {
	api, handler, access, admin := setupSiteAdmin(t, "remote-multi")
	users := map[string]*http.Cookie{}
	for _, account := range []string{"student-a", "student-b"} {
		response := requestWithSiteCookies(handler, "POST", "/api/admin/accounts", `{"account":"`+account+`"}`, access, admin)
		if response.Code != 200 {
			t.Fatal(response)
		}
		response = requestWithSiteCookies(handler, "POST", "/api/login", `{"username":"`+account+`","password":"test-only-school-password","remember":true}`, access)
		if response.Code != 200 {
			t.Fatal(response)
		}
		users[account] = responseCookie(t, response.Result(), sessionCookie)
		response = requestWithSiteCookies(handler, "PUT", "/api/documents/gpa/plan", `{"revision":0,"data":{"name":"private-`+account+`"}}`, access, users[account])
		if response.Code != 200 {
			t.Fatal(response)
		}
	}
	response := requestWithSiteCookies(handler, "GET", "/api/admin/accounts", "", access, admin)
	if response.Code != 200 || strings.Contains(response.Body.String(), "password") || strings.Contains(response.Body.String(), "private-") {
		t.Fatal("admin list leaked user data", response)
	}
	if response := requestWithSiteCookies(handler, "GET", "/api/documents/gpa/plan", "", access, admin); response.Code != 401 {
		t.Fatal("admin bypassed user identity", response)
	}
	if response := requestWithSiteCookies(handler, "GET", "/api/admin/accounts", "", access, users["student-a"]); response.Code != 401 {
		t.Fatal("ordinary user became administrator", response)
	}
	ctx := context.Background()
	session, _ := api.database.Session(ctx, users["student-a"].Value)
	original := api.accounts.Runtime(session)
	task := json.RawMessage(`{"status":"running","desired_state":"running","pending_selection":{"class_id":"unknown-write"}}`)
	other, err := api.database.Session(ctx, users["student-b"].Value)
	if err != nil {
		t.Fatal(err)
	}
	for _, active := range []store.Session{session, other} {
		if _, err := api.database.SaveForSession(ctx, active, "jwxk-automation-tasks", "task", task, 0, false); err != nil {
			t.Fatal(err)
		}
		if queued, err := api.database.QueueMail(ctx, active, "test", "event", json.RawMessage(`{"subject":"test","body":"test"}`)); err != nil || !queued {
			t.Fatal("failed to queue fixture", err)
		}
	}
	response = requestWithSiteCookies(handler, "POST", "/api/admin/accounts/student-a/stop", `{}`, access, admin)
	if response.Code != 200 || original.Context.Err() == nil {
		t.Fatal("admin stop failed", response)
	}
	if response := requestWithSiteCookies(handler, "GET", "/api/documents/gpa/plan", "", access, users["student-a"]); response.Code != 401 {
		t.Fatal("stopped account retained access", response)
	}
	response = requestWithSiteCookies(handler, "GET", "/api/documents/gpa/plan", "", access, users["student-b"])
	if response.Code != 200 || !strings.Contains(response.Body.String(), "private-student-b") || strings.Contains(response.Body.String(), "private-student-a") {
		t.Fatal("other account affected or exposed", response)
	}
	document, err := api.database.Read(ctx, "student-a", "jwxk-automation-tasks", "task")
	if err != nil || !strings.Contains(string(document.Payload), `"desired_state":"paused"`) || !strings.Contains(string(document.Payload), "unknown-write") {
		t.Fatal("stop lost write evidence or kept task runnable", err, string(document.Payload))
	}
	otherTask, err := api.database.Read(ctx, other.Account, "jwxk-automation-tasks", "task")
	if err != nil || string(otherTask.Payload) != string(task) {
		t.Fatal("other task changed", err, string(otherTask.Payload))
	}
	if due, err := api.database.DueMailAccounts(ctx, time.Now()); err != nil || len(due) != 1 || due[0].Account != other.Account {
		t.Fatal("only the unaffected account should have mail due", due, err)
	}
	for _, session := range mustRestorable(t, api.database) {
		if session.Account == "student-a" {
			t.Fatal("stopped account would resume after restart")
		}
	}
	response = requestWithSiteCookies(handler, "POST", "/api/admin/accounts/student-a/disable", `{}`, access, admin)
	if response.Code != 200 {
		t.Fatal(response)
	}
	response = requestWithSiteCookies(handler, "POST", "/api/login", `{"username":"student-a","password":"test-only"}`, access)
	if response.Code != 403 {
		t.Fatal("disabled user logged in", response)
	}
	response = requestWithSiteCookies(handler, "POST", "/api/admin/accounts/student-a/enable", `{}`, access, admin)
	if response.Code != 200 {
		t.Fatal(response)
	}
	response = requestWithSiteCookies(handler, "POST", "/api/login", `{"username":"student-a","password":"test-only"}`, access)
	cookie := responseCookie(t, response.Result(), sessionCookie)
	response = requestWithSiteCookies(handler, "GET", "/api/documents/gpa/plan", "", access, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "private-student-a") {
		t.Fatal("disable removed user data", response)
	}
}

func mustRestorable(t *testing.T, database *store.Store) []store.Session {
	t.Helper()
	sessions, err := database.RestorableAccounts(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return sessions
}

func TestRemoteSingleAllowsOnlyOneEnabledAccount(t *testing.T) {
	_, handler, access, admin := setupSiteAdmin(t, "remote-single")
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/accounts", `{"account":"first"}`, access, admin); response.Code != 200 {
		t.Fatal(response)
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/accounts", `{"account":"second"}`, access, admin); response.Code != 409 {
		t.Fatal(response)
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/accounts/first/remove", `{}`, access, admin); response.Code != 200 {
		t.Fatal(response)
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/accounts", `{"account":"second"}`, access, admin); response.Code != 200 {
		t.Fatal(response)
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/accounts/first/enable", `{}`, access, admin); response.Code != 409 {
		t.Fatal(response)
	}
}

func TestUnboundMultiAccountDirectoryRejectsSingleUserModes(t *testing.T) {
	database, err := store.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	for _, account := range []string{"first", "second"} {
		if err := database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	for _, mode := range []string{"local-single", "remote-single"} {
		if accounts, err := NewAccounts(database, mode); err == nil {
			accounts.Close()
			t.Fatalf("accepted %s", mode)
		}
		if _, err := database.SiteSetting(ctx, "deployment-mode"); !errors.Is(err, store.ErrMissing) {
			t.Fatal("refused initialization bound the data directory", err)
		}
	}
	accounts, err := NewAccounts(database, "remote-multi")
	if err != nil {
		t.Fatal(err)
	}
	defer accounts.Close()
	mode, err := database.SiteSetting(ctx, "deployment-mode")
	if err != nil || string(mode) != "remote-multi" {
		t.Fatal(string(mode), err)
	}
}

func TestSitePasswordFailureRateLimit(t *testing.T) {
	_, handler, access, _ := setupSiteAdmin(t, "remote-multi")
	for attempt := 0; attempt < 5; attempt++ {
		if response := requestWithSiteCookies(handler, "POST", "/api/admin/login", `{"password":"wrong"}`, access); response.Code != 401 {
			t.Fatal(response)
		}
	}
	if response := requestWithSiteCookies(handler, "POST", "/api/admin/login", `{"password":"test-admin-password"}`, access); response.Code != 429 {
		t.Fatal("missing password rate limit", response)
	}
}

func TestTrustedProxySourceIgnoresUntrustedForwardedHeaders(t *testing.T) {
	api := &API{}
	if err := api.SetTrustedProxies("127.0.0.1/32,10.0.0.0/24"); err != nil {
		t.Fatal(err)
	}
	for _, item := range []struct{ peer, header, expected string }{
		{"203.0.113.1:1234", "198.51.100.5", "203.0.113.1"},
		{"127.0.0.1:1234", "203.0.113.99, 198.51.100.5", "198.51.100.5"},
		{"127.0.0.1:1234", "198.51.100.5, 10.0.0.3", "198.51.100.5"},
		{"127.0.0.1:1234", "invalid", "127.0.0.1"},
	} {
		request := httptest.NewRequest("GET", "http://localhost/api/status", nil)
		request.RemoteAddr = item.peer
		request.Header.Set("X-Forwarded-For", item.header)
		if source := api.clientSource(request); source != item.expected {
			t.Fatal(source, item.expected)
		}
	}
	if err := api.SetTrustedProxies("0.0.0.0/0"); err == nil {
		t.Fatal("unrestricted proxy trust accepted")
	}
}
