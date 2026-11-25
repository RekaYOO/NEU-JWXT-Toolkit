package application

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type testVPNClient struct {
	testCampusClient
	polls, sends, verifies   int
	pollStarted, pollRelease chan struct{}
	target                   string
}

func (client *testVPNClient) SetVPNTarget(target string) error { client.target = target; return nil }

func (client *testVPNClient) Status() (bool, string)                   { return client.active.Load(), "webvpn" }
func (client *testVPNClient) Snapshot() ([]campus.SavedCookie, string) { return nil, "webvpn" }
func (client *testVPNClient) StartVPNQR(context.Context, string) (map[string]any, error) {
	return map[string]any{"flow_id": "test-flow", "status": "pending", "qr_content": "test-only", "poll_interval": 3, "expires_in": 180}, nil
}
func (client *testVPNClient) PollVPNQR(context.Context, string) (map[string]any, error) {
	client.polls++
	if client.pollStarted != nil {
		close(client.pollStarted)
		<-client.pollRelease
	}
	client.active.Store(true)
	return map[string]any{"status": "authenticated", "username": client.account}, nil
}
func (client *testVPNClient) StartVPNPassword(context.Context, string) (map[string]any, error) {
	return map[string]any{"status": "sms_required", "flow_id": "test-flow", "expires_in": 300, "captcha_image": "data:image/gif;base64,dGVzdA=="}, nil
}
func (client *testVPNClient) RefreshVPNCaptcha(context.Context, string) (map[string]any, error) {
	return map[string]any{"status": "captcha_refreshed", "expires_in": 300, "captcha_image": "data:image/gif;base64,dGVzdA=="}, nil
}
func (client *testVPNClient) SendVPNSMS(context.Context, string, string) (map[string]any, error) {
	client.sends++
	return map[string]any{"status": "sent", "expires_in": 300}, nil
}
func (client *testVPNClient) VerifyVPNSMS(context.Context, string, string) (map[string]any, error) {
	client.verifies++
	client.active.Store(true)
	return map[string]any{"status": "authenticated", "username": client.account}, nil
}

type cancelledPasswordClient struct {
	testVPNClient
	cancelRequest  context.CancelFunc
	observedCancel bool
}

func (client *cancelledPasswordClient) StartVPNPassword(ctx context.Context, password string) (map[string]any, error) {
	client.cancelRequest()
	select {
	case <-ctx.Done():
		client.observedCancel = true
	case <-time.After(time.Second):
	}
	return client.testVPNClient.StartVPNPassword(ctx, password)
}

func TestCancelledVPNPasswordStartDiscardsCandidate(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student"); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	client := &cancelledPasswordClient{cancelRequest: cancel}
	client.active.Store(true)
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
	request := httptest.NewRequest(http.MethodPost, "/api/webvpn/password/start", strings.NewReader(`{"username":"student","password":"fixture"}`)).WithContext(ctx)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if !client.observedCancel {
		t.Fatal("school request did not observe cancellation")
	}
	if len(api.vpnFlows) != 0 || client.active.Load() || response.Body.Len() != 0 {
		t.Fatal("cancelled request published or retained its VPN candidate")
	}
}

func responseCookie(t *testing.T, response *http.Response, name string) *http.Cookie {
	t.Helper()
	for _, cookie := range response.Cookies() {
		if cookie.Name == name {
			return cookie
		}
	}
	t.Fatal("missing cookie", name)
	return nil
}

func TestVPNBrowserFlowOwnershipAndAuthenticatedSession(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student")
	client := &testVPNClient{testCampusClient: testCampusClient{account: "student"}}
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
	response := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{}`, nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"success":true`) {
		t.Fatal(response)
	}
	owner := responseCookie(t, response.Result(), vpnOwnerCookie)
	if !owner.HttpOnly || owner.SameSite != http.SameSiteStrictMode {
		t.Fatal("unsafe flow cookie")
	}
	for _, cookie := range []*http.Cookie{nil, {Name: vpnOwnerCookie, Value: strings.Repeat("a", 64)}} {
		response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, cookie)
		if !strings.Contains(response.Body.String(), "WEBVPN_FLOW_MISSING") || client.polls != 0 {
			t.Fatal(response)
		}
	}
	response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"other-flow"}`, owner)
	if !strings.Contains(response.Body.String(), "WEBVPN_FLOW_REPLACED") || client.polls != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, owner)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"authenticated"`) || client.polls != 1 {
		t.Fatal(response)
	}
	cookie := responseCookie(t, response.Result(), sessionCookie)
	response = apiRequest(handler, "GET", "/api/status", "", cookie)
	if !strings.Contains(response.Body.String(), `"is_logged_in":true`) || !strings.Contains(response.Body.String(), `"network_mode":"webvpn"`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, owner)
	if !strings.Contains(response.Body.String(), "WEBVPN_FLOW_MISSING") {
		t.Fatal(response)
	}
}

type directQRTestClient struct {
	testVPNClient
	mode, provider string
}

func (client *directQRTestClient) StartQR(ctx context.Context, provider, mode string) (map[string]any, error) {
	client.provider, client.mode = provider, mode
	return client.StartVPNQR(ctx, provider)
}

func (client *directQRTestClient) Status() (bool, string) {
	return client.active.Load(), client.mode
}

func (client *directQRTestClient) Snapshot() ([]campus.SavedCookie, string) {
	return nil, client.mode
}

func TestDirectQRBrowserLoginPublishesDirectSession(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student"); err != nil {
		t.Fatal(err)
	}
	client := &directQRTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}}
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }

	for _, invalid := range []string{`{"network_mode":"offline"}`, `{"network_mode":"direct","target_service":"jwxk"}`} {
		response := apiRequest(handler, "POST", "/api/webvpn/qr/start", invalid, nil)
		if response.Code != 422 || client.mode != "" {
			t.Fatal(response.Code, client.mode)
		}
	}
	response := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"network_mode":"direct","qr_provider":"smart_neu"}`, nil)
	if response.Code != 200 || client.mode != "direct" || client.provider != "smart_neu" {
		t.Fatal(response.Code, client.mode, client.provider)
	}
	owner := responseCookie(t, response.Result(), vpnOwnerCookie)
	response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, owner)
	cookie := responseCookie(t, response.Result(), sessionCookie)
	response = apiRequest(handler, "GET", "/api/status", "", cookie)
	if !strings.Contains(response.Body.String(), `"is_logged_in":true`) || !strings.Contains(response.Body.String(), `"network_mode":"direct"`) {
		t.Fatal(response)
	}
}

func TestVPNConcurrentBrowserStartsKeepSingleChallenge(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student"); err != nil {
		t.Fatal(err)
	}
	ready := make(chan struct{}, 2)
	release := make(chan struct{})
	api.vpnFactory = func(account string) (VPNClient, error) {
		ready <- struct{}{}
		<-release
		return &testVPNClient{testCampusClient: testCampusClient{account: account}}, nil
	}
	results := make(chan *httptest.ResponseRecorder, 2)
	for index := 0; index < 2; index++ {
		go func() {
			results <- apiRequest(handler, "POST", "/api/webvpn/password/start", `{"username":"student","password":"private-test"}`, nil)
		}()
	}
	for index := 0; index < 2; index++ {
		select {
		case <-ready:
		case <-time.After(5 * time.Second):
			close(release)
			t.Fatal("both browser requests must reach the registration barrier")
		}
	}
	close(release)
	var owner *http.Cookie
	rejected := 0
	for index := 0; index < 2; index++ {
		response := <-results
		switch {
		case strings.Contains(response.Body.String(), `"status":"sms_required"`):
			owner = responseCookie(t, response.Result(), vpnOwnerCookie)
		case strings.Contains(response.Body.String(), "SERVICE_INTERACTION_REQUIRED") && len(response.Result().Cookies()) == 0:
			rejected++
		default:
			t.Fatal(response)
		}
	}
	if owner == nil || rejected != 1 {
		t.Fatal("expected one challenge owner and one rejected request")
	}
	response := apiRequest(handler, "POST", "/api/webvpn/sms/captcha/refresh", `{"flow_id":"test-flow"}`, owner)
	if !strings.Contains(response.Body.String(), `"status":"captcha_refreshed"`) {
		t.Fatal(response)
	}
}

func TestVPNPasswordPendingIsLocalAndRememberedOnlyAfterAuthentication(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student")
	client := &testVPNClient{testCampusClient: testCampusClient{account: "student"}}
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
	response := apiRequest(handler, "POST", "/api/webvpn/password/start", `{"username":"student","password":"private-test","remember":true}`, nil)
	owner := responseCookie(t, response.Result(), vpnOwnerCookie)
	if _, err := api.database.ReadSecret(context.Background(), "student", "campus-session"); err == nil {
		t.Fatal("persisted unverified credential")
	}
	response = apiRequest(handler, "GET", "/api/auth/pending", "", owner)
	if !strings.Contains(response.Body.String(), `"required":true`) || strings.Contains(response.Body.String(), "private-test") || client.sends != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/sms/captcha/refresh", `{"flow_id":"test-flow"}`, owner)
	if response.Code != 200 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/sms/send", `{"flow_id":"test-flow","captcha_code":"manual"}`, owner)
	if client.sends != 1 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", "/api/auth/pending", "", owner)
	if !strings.Contains(response.Body.String(), `"flow_id":"test-flow"`) || !strings.Contains(response.Body.String(), `"required":true`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/sms/verify", `{"flow_id":"test-flow","code":"manual","trust_device":false}`, owner)
	if !strings.Contains(response.Body.String(), `"success":true`) || client.verifies != 1 {
		t.Fatal(response)
	}
	responseCookie(t, response.Result(), sessionCookie)
	payload, err := api.database.ReadSecret(context.Background(), "student", "campus-session")
	var saved map[string]any
	if err != nil || json.Unmarshal(payload, &saved) != nil || saved["password"] != "private-test" || saved["mode"] != "webvpn" {
		t.Fatal("remembered session incorrect", err)
	}
}

func TestVPNRevocationAndLogoutInvalidateCandidate(t *testing.T) {
	for _, action := range []string{"revoke", "logout", "cancel"} {
		t.Run(action, func(t *testing.T) {
			api, handler := testApplication(t, "remote-multi")
			api.database.AddAccount(context.Background(), "student")
			client := &testVPNClient{testCampusClient: testCampusClient{account: "student"}}
			api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
			response := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"username":"student"}`, nil)
			owner := responseCookie(t, response.Result(), vpnOwnerCookie)
			switch action {
			case "revoke":
				api.database.RevokeAccount(context.Background(), "student")
			case "logout":
				apiRequest(handler, "POST", "/api/logout", `{}`, owner)
			case "cancel":
				apiRequest(handler, "POST", "/api/webvpn/qr/cancel", `{"flow_id":"test-flow"}`, owner)
			}
			response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, owner)
			if strings.Contains(response.Body.String(), `"success":true`) || client.polls != 0 {
				t.Fatal(response)
			}
		})
	}
}

type testGatewayClient struct {
	testCampusClient
	merges int
}

type recoveryGatewayReader struct{ testGatewayClient }

func (client *recoveryGatewayReader) RequestService(context.Context, campus.ServiceRequest) ([]byte, error) {
	if client.merges == 0 {
		return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED", ConfirmedServiceExpiry: true}
	}
	return []byte("records"), nil
}

func TestAutomaticGatewayChallengeContinuesWithExistingSession(t *testing.T) {
	for _, target := range []string{"jwxk", "cxcy"} {
		t.Run(target, func(t *testing.T) {
			ctx := context.Background()
			api, handler := testApplication(t, "remote-multi")
			if err := api.database.AddAccount(ctx, "student"); err != nil {
				t.Fatal(err)
			}
			generation, err := api.database.Generation(ctx, "student")
			if err != nil {
				t.Fatal(err)
			}
			primary := &recoveryGatewayReader{}
			primary.account = "student"
			primary.active.Store(true)
			if err := api.accounts.installClient(ctx, primary, "test-only", true, generation); err != nil {
				t.Fatal(err)
			}
			token, err := api.accounts.CreateSession(ctx, "student", time.Hour)
			if err != nil {
				t.Fatal(err)
			}
			cookie := &http.Cookie{Name: sessionCookie, Value: token}
			candidate := &testVPNClient{testCampusClient: testCampusClient{account: "student"}}
			starts := 0
			api.vpnFactory = func(string) (VPNClient, error) { starts++; return candidate, nil }
			runtime := api.accounts.Runtime(store.Session{Account: "student", Generation: generation})
			input := campus.ServiceRequest{Service: target, Mode: "webvpn", ReadOnly: true}
			expiredContext, expire := context.WithCancel(runtime.Context)
			defer expire()
			api.vpnMutex.Lock()
			api.pendingVPN["student"] = &browserVPNFlow{client: &testVPNClient{}, context: expiredContext, cancel: expire, created: time.Now(), original: runtime, target: target, bound: store.Session{Account: "student", Generation: generation}, state: map[string]any{"expires_at": time.Now().Add(-2 * time.Minute).Unix()}}
			api.vpnMutex.Unlock()
			other := &browserVPNFlow{registeredAccount: "other", context: ctx, created: time.Now()}
			other.mutex.Lock()
			api.vpnMutex.Lock()
			api.vpnFlows["other-browser"] = other
			api.vpnMutex.Unlock()
			defer func() {
				api.vpnMutex.Lock()
				delete(api.vpnFlows, "other-browser")
				api.vpnMutex.Unlock()
				other.mutex.Unlock()
			}()
			_, err = runtime.RequestService(ctx, input)
			if expiredContext.Err() == nil {
				t.Fatal("expired challenge was not released before retry")
			}
			if failure, ok := err.(*campus.Error); !ok || failure.Code != "SERVICE_INTERACTION_REQUIRED" || candidate.target != target {
				t.Fatal(err)
			}
			pending := apiRequest(handler, "GET", "/api/auth/pending", "", cookie)
			manual := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"target_service":"`+target+`"}`, cookie)
			if !strings.Contains(manual.Body.String(), "SERVICE_INTERACTION_REQUIRED") || starts != 1 || len(manual.Result().Cookies()) != 0 {
				t.Fatal("manual start replaced background verification", manual)
			}
			if !strings.Contains(pending.Body.String(), `"flow_id":"test-flow"`) || starts != 1 || candidate.sends != 0 {
				t.Fatal(pending)
			}
			sent := apiRequest(handler, "POST", "/api/webvpn/sms/send", `{"flow_id":"test-flow","captcha_code":"manual"}`, cookie)
			if candidate.sends != 1 {
				t.Fatal(sent)
			}
			verified := apiRequest(handler, "POST", "/api/webvpn/sms/verify", `{"flow_id":"test-flow","code":"manual"}`, cookie)
			if candidate.verifies != 1 || primary.merges != 1 || !strings.Contains(verified.Body.String(), `"success":true`) || len(verified.Result().Cookies()) != 0 {
				t.Fatal(verified)
			}
			data, err := runtime.RequestService(ctx, input)
			if err != nil || string(data) != "records" || starts != 1 {
				t.Fatal(err, starts)
			}
		})
	}
}

func (client *testGatewayClient) AdoptVPNGateway(account string, cookies []campus.SavedCookie) error {
	client.merges++
	return nil
}

func TestVPNServiceRecoveryKeepsPrimarySessionAndMode(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student")
	primary := &testGatewayClient{testCampusClient: testCampusClient{account: "student"}}
	api.accounts.factory = func(string) (CampusClient, error) { return primary, nil }
	sessionCookie := loginCookie(t, handler, "student")
	candidate := &testVPNClient{testCampusClient: testCampusClient{account: "student"}}
	api.vpnFactory = func(string) (VPNClient, error) { return candidate, nil }
	response := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"target_service":"jwxk"}`, nil)
	if response.Code != 401 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"username":"other","target_service":"jwxk"}`, sessionCookie)
	if !strings.Contains(response.Body.String(), "WEBVPN_ACCESS_DENIED") {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"target_service":"jwxk"}`, sessionCookie)
	owner := responseCookie(t, response.Result(), vpnOwnerCookie)
	if err := api.database.AddAccount(context.Background(), "other"); err != nil {
		t.Fatal(err)
	}
	api.accounts.factory = func(string) (CampusClient, error) {
		return &testCampusClient{account: "other"}, nil
	}
	otherSession := loginCookie(t, handler, "other")
	for _, invalidSession := range []*http.Cookie{nil, otherSession} {
		probe := httptest.NewRequest("POST", "/api/webvpn/qr/status", strings.NewReader(`{"flow_id":"test-flow"}`))
		probe.Header.Set("Content-Type", "application/json")
		probe.AddCookie(owner)
		if invalidSession != nil {
			probe.AddCookie(invalidSession)
		}
		denied := httptest.NewRecorder()
		handler.ServeHTTP(denied, probe)
		if !strings.Contains(denied.Body.String(), "WEBVPN_ACCESS_DENIED") || candidate.polls != 0 || primary.merges != 0 {
			t.Fatal("service challenge crossed session boundary", denied)
		}
	}
	request := httptest.NewRequest("POST", "http://localhost/api/webvpn/qr/status", strings.NewReader(`{"flow_id":"test-flow"}`))
	request.Header.Set("Content-Type", "application/json")
	request.AddCookie(owner)
	request.AddCookie(sessionCookie)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if !strings.Contains(response.Body.String(), `"success":true`) || primary.merges != 1 || candidate.target != "jwxk" {
		t.Fatal(response)
	}
	if len(response.Result().Cookies()) != 0 {
		t.Fatal("service recovery replaced primary browser session")
	}
	response = apiRequest(handler, "GET", "/api/status", "", sessionCookie)
	if !strings.Contains(response.Body.String(), `"network_mode":"direct"`) || !strings.Contains(response.Body.String(), `"is_logged_in":true`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/password/start", `{"username":"student","password":"private-test","target_service":"cxcy"}`, sessionCookie)
	owner = responseCookie(t, response.Result(), vpnOwnerCookie)
	for _, current := range []*http.Cookie{nil, otherSession, sessionCookie} {
		probe := httptest.NewRequest("GET", "/api/auth/pending", nil)
		probe.AddCookie(owner)
		if current != nil {
			probe.AddCookie(current)
		}
		details := httptest.NewRecorder()
		handler.ServeHTTP(details, probe)
		var state map[string]any
		if err := json.Unmarshal(details.Body.Bytes(), &state); err != nil || details.Code != http.StatusOK {
			t.Fatal(details)
		}
		if current == sessionCookie {
			if state["required"] != true || state["target_service"] != "cxcy" || state["captcha_image"] != "data:image/gif;base64,dGVzdA==" {
				t.Fatal("bound account lost pending captcha", state)
			}
		} else if len(state) != 1 || state["required"] != false {
			t.Fatal("pending challenge exposed outside account", state)
		}
	}
}

func TestVPNVerifiedButUnregisteredAccountCannotEnterRemoteMode(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	client := &testVPNClient{testCampusClient: testCampusClient{account: "unregistered"}}
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
	response := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{}`, nil)
	owner := responseCookie(t, response.Result(), vpnOwnerCookie)
	response = apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, owner)
	if !strings.Contains(response.Body.String(), "WEBVPN_ACCESS_DENIED") || len(response.Result().Cookies()) != 0 {
		t.Fatal(response)
	}
	if active, _ := client.Status(); active {
		t.Fatal("unregistered candidate retained")
	}
}

func TestVPNLogoutRejectsLateSuccessfulCallback(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student")
	client := &testVPNClient{testCampusClient: testCampusClient{account: "student"}, pollStarted: make(chan struct{}), pollRelease: make(chan struct{})}
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
	response := apiRequest(handler, "POST", "/api/webvpn/qr/start", `{"username":"student"}`, nil)
	owner := responseCookie(t, response.Result(), vpnOwnerCookie)
	api.vpnMutex.Lock()
	flow := api.vpnFlows[owner.Value]
	api.vpnMutex.Unlock()
	polled := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		polled <- apiRequest(handler, "POST", "/api/webvpn/qr/status", `{"flow_id":"test-flow"}`, owner)
	}()
	select {
	case <-client.pollStarted:
	case <-time.After(2 * time.Second):
		t.Fatal("poll did not start")
	}
	loggedOut := make(chan *httptest.ResponseRecorder, 1)
	go func() { loggedOut <- apiRequest(handler, "POST", "/api/logout", `{}`, owner) }()
	select {
	case <-flow.context.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("logout did not cancel flow")
	}
	close(client.pollRelease)
	response = <-polled
	if !strings.Contains(response.Body.String(), "WEBVPN_ACCESS_DENIED") || len(response.Result().Cookies()) != 0 {
		t.Fatal("late callback authenticated", response)
	}
	if response = <-loggedOut; response.Code != 200 {
		t.Fatal(response)
	}
	api.accounts.mutex.RLock()
	runtime := api.accounts.runtimes["student"]
	api.accounts.mutex.RUnlock()
	if runtime != nil {
		t.Fatal("late callback installed runtime")
	}
}

func TestVPNLogoutRevokesInstalledCandidateBeforeBrowserReceivesSession(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student")
	client := &testVPNClient{testCampusClient: testCampusClient{account: "student"}}
	client.active.Store(true)
	generation, _ := api.database.Generation(ctx, "student")
	if err := api.accounts.installClient(ctx, client, "", false, generation); err != nil {
		t.Fatal(err)
	}
	token, err := api.accounts.CreateSession(ctx, "student", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	flowContext, cancel := context.WithCancel(api.accounts.context)
	owner := strings.Repeat("b", 64)
	api.vpnFlows[owner] = &browserVPNFlow{client: client, context: flowContext, cancel: cancel, adopted: true, bound: store.Session{Account: "student", Generation: generation}}
	response := apiRequest(handler, "POST", "/api/logout", `{}`, &http.Cookie{Name: vpnOwnerCookie, Value: owner})
	if response.Code != 200 {
		t.Fatal(response)
	}
	if _, err := api.database.Session(ctx, token); err == nil {
		t.Fatal("logout left late browser token usable")
	}
}
