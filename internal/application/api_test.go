package application

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type testCampusClient struct {
	account string
	active  atomic.Bool
}

func (client *testCampusClient) Account() string        { return client.account }
func (client *testCampusClient) Status() (bool, string) { return client.active.Load(), "direct" }
func (client *testCampusClient) LoginDirect(context.Context, string) error {
	client.active.Store(true)
	return nil
}
func (client *testCampusClient) Close()                                   { client.active.Store(false) }
func (client *testCampusClient) Snapshot() ([]campus.SavedCookie, string) { return nil, "direct" }
func (client *testCampusClient) Request(ctx context.Context, method, target string, form url.Values) ([]byte, error) {
	return nil, ctx.Err()
}

func testApplication(t *testing.T, mode string) (*API, http.Handler) {
	t.Helper()
	return testApplicationAt(t, mode, t.TempDir())
}

func testApplicationAt(t *testing.T, mode, directory string) (*API, http.Handler) {
	t.Helper()
	database, err := store.Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	accounts, err := NewAccounts(database, mode)
	if err != nil {
		t.Fatal(err)
	}
	accounts.factory = func(account string) (CampusClient, error) { return &testCampusClient{account: account}, nil }
	api := NewAPI(database, accounts, "test")
	api.gatewayRequired = false
	router := http.NewServeMux()
	api.Register(router)
	t.Cleanup(func() { accounts.Close(); database.Close() })
	return api, api.Boundary(router)
}

func apiRequest(handler http.Handler, method, path, body string, cookie *http.Cookie) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, "http://localhost"+path, strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	if cookie != nil {
		request.AddCookie(cookie)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func loginCookie(t *testing.T, handler http.Handler, account string) *http.Cookie {
	t.Helper()
	response := apiRequest(handler, "POST", "/api/login", `{"username":"`+account+`","password":"test-only","remember":true}`, nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"success":true`) {
		t.Fatalf("login failed: %d %s", response.Code, response.Body.String())
	}
	cookies := response.Result().Cookies()
	if len(cookies) != 1 || !cookies[0].HttpOnly || cookies[0].SameSite != http.SameSiteLaxMode {
		t.Fatal("missing protected session cookie")
	}
	return cookies[0]
}

func TestTwoAccountHTTPIsolationLogoutAndRevocation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(context.Background(), account); err != nil {
			t.Fatal(err)
		}
	}
	cookieA := loginCookie(t, handler, "student-a")
	cookieB := loginCookie(t, handler, "student-b")
	saved := apiRequest(handler, "PUT", "/api/documents/gpa/plan", `{"revision":0,"data":{"name":"A private plan"}}`, cookieA)
	if saved.Code != 200 {
		t.Fatal(saved.Body.String())
	}
	if response := apiRequest(handler, "GET", "/api/documents/gpa/plan", "", cookieB); response.Code != 404 {
		t.Fatal("other account read private document")
	}
	if response := apiRequest(handler, "PUT", "/api/documents/gpa/plan", `{"revision":1,"data":{},"account":"student-a"}`, cookieB); response.Code != 422 {
		t.Fatal("account override accepted")
	}
	if response := apiRequest(handler, "GET", "/api/documents/gpa/plan", "", nil); response.Code != 401 {
		t.Fatal("unauthenticated read accepted")
	}
	if response := apiRequest(handler, "POST", "/api/logout", "", cookieA); response.Code != 200 {
		t.Fatal("logout failed")
	}
	if response := apiRequest(handler, "GET", "/api/documents/gpa/plan", "", cookieA); response.Code != 401 {
		t.Fatal("logged out cookie still works")
	}
	response := apiRequest(handler, "GET", "/api/client/bootstrap", "", cookieB)
	if !strings.Contains(response.Body.String(), `"current_user":"student-b"`) || !strings.Contains(response.Body.String(), `"is_logged_in":true`) {
		t.Fatal("one account logout affected another")
	}
	if err := api.database.SetEnabled(context.Background(), "student-b", false); err != nil {
		t.Fatal(err)
	}
	if response := apiRequest(handler, "PUT", "/api/documents/theme/current", `{"revision":0,"data":{}}`, cookieB); response.Code != 401 {
		t.Fatal("disabled account still writes")
	}
}

func TestSingleAccountSwitchAndCrossSiteProtection(t *testing.T) {
	_, handler := testApplication(t, "local-single")
	cookieA := loginCookie(t, handler, "student-a")
	loginCookie(t, handler, "student-b")
	if response := apiRequest(handler, "GET", "/api/documents/gpa/plan", "", cookieA); response.Code != 401 {
		t.Fatal("previous account remained active after switch")
	}
	request := httptest.NewRequest("POST", "http://localhost/api/login", strings.NewReader(`{}`))
	request.Header.Set("Origin", "https://untrusted.example")
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 403 {
		t.Fatal("cross-site write accepted")
	}
	request = httptest.NewRequest("GET", "http://untrusted.example/api/status", nil)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 403 {
		t.Fatal("local DNS rebinding host accepted")
	}
}

func TestDocumentConflictPreservesDraftAndTombstone(t *testing.T) {
	_, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	apiRequest(handler, "PUT", "/api/documents/selection-plans/term", `{"revision":0,"data":{"event":"saved"}}`, cookie)
	response := apiRequest(handler, "PUT", "/api/documents/selection-plans/term", `{"revision":0,"data":{"event":"stale"}}`, cookie)
	if response.Code != 409 {
		t.Fatal("stale revision accepted")
	}
	response = apiRequest(handler, "DELETE", "/api/documents/selection-plans/term", `{"revision":1}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "PUT", "/api/documents/selection-plans/term", `{"revision":0,"data":{"event":"resurrected"}}`, cookie)
	if response.Code != 409 {
		t.Fatal("stale draft resurrected deletion")
	}
	response = apiRequest(handler, "GET", "/api/documents/selection-plans/term", "", cookie)
	var document store.Document
	if err := json.NewDecoder(io.NopCloser(response.Body)).Decode(&document); err != nil || !document.Deleted || document.Revision != 2 {
		t.Fatal("missing deletion marker")
	}
}

type pendingLoginClient struct {
	testCampusClient
	started chan struct{}
	release chan struct{}
}

func (client *pendingLoginClient) LoginDirect(ctx context.Context, password string) error {
	close(client.started)
	select {
	case <-client.release:
		client.active.Store(true)
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func TestStopDuringLoginCannotResurrectAccount(t *testing.T) {
	api, _ := testApplication(t, "local-single")
	client := &pendingLoginClient{testCampusClient: testCampusClient{account: "student"}, started: make(chan struct{}), release: make(chan struct{})}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	result := make(chan error, 1)
	go func() { result <- api.accounts.Login(context.Background(), "student", "test-only", true) }()
	select {
	case <-client.started:
	case <-time.After(time.Second):
		t.Fatal("login did not start")
	}
	if err := api.accounts.Stop(context.Background(), "student", true); err != nil {
		t.Fatal(err)
	}
	close(client.release)
	if err := <-result; !errors.Is(err, store.ErrAccess) {
		t.Fatalf("stopped login was accepted: %v", err)
	}
	if _, err := api.database.ReadSecret(context.Background(), "student", "campus-session"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("late login restored saved credentials")
	}
	if _, err := api.accounts.CreateSession(context.Background(), "student", time.Hour); !errors.Is(err, store.ErrAccess) {
		t.Fatal("stopped login issued a fresh browser session")
	}
}
