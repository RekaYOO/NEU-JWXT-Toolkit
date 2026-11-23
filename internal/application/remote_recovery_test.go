package application

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func remoteRecoveryFixture(t *testing.T) (*API, http.Handler, store.Session, string, *testVPNClient) {
	t.Helper()
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	if err := api.database.AddAccount(ctx, "20990001"); err != nil {
		t.Fatal(err)
	}
	cookie := loginCookie(t, handler, "20990001")
	response := apiRequest(handler, "PUT", "/api/system-settings/auth-recovery", `{"public_base_url":"https://toolbox.example.invalid"}`, cookie)
	if response.Code != 200 {
		t.Fatal(response)
	}
	session := store.Session{Account: "20990001", Generation: 1}
	settings, _, _ := api.readRecoverySettings(ctx, session)
	recovery, err := api.database.CreateRecoveryContext(ctx, session, "primary", "tracking", "lost", settings.Epoch, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	token, err := api.database.RecoveryToken(ctx, recovery.ID)
	if err != nil {
		t.Fatal(err)
	}
	client := &testVPNClient{testCampusClient: testCampusClient{account: session.Account}}
	api.vpnFactory = func(string) (VPNClient, error) { return client, nil }
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mailTick = nil
	api.accounts.mutex.Unlock()
	api.gatewayRequired = true
	return api, handler, session, token, client
}

type recoveryCaptchaClient struct{ testVPNClient }

func (client *recoveryCaptchaClient) SendVPNSMS(context.Context, string, string) (map[string]any, error) {
	client.sends++
	return map[string]any{"status": "sms_required", "flow_id": "refreshed-private-flow", "captcha_image": "data:image/png;base64,bmV3", "expires_in": 180, "username": "must-not-expose"}, &campus.Error{Code: "WEBVPN_CAPTCHA_INVALID", Message: "图形验证码错误，请重新填写"}
}

func TestRemoteRecoveryPreservesRefreshedCaptchaOnInvalidInput(t *testing.T) {
	api, handler, session, token, _ := remoteRecoveryFixture(t)
	client := &recoveryCaptchaClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: session.Account}}}
	ctx, cancel := context.WithCancel(api.accounts.context)
	defer cancel()
	flow := &browserVPNFlow{client: client, context: ctx, cancel: cancel, created: time.Now(), identifier: "original-private-flow", target: "primary", remember: true, bound: session, original: api.accounts.Runtime(session), state: map[string]any{"status": "sms_required", "captcha_image": "old", "expires_at": time.Now().Add(5 * time.Minute).Unix()}}
	api.vpnMutex.Lock()
	api.pendingVPN[session.Account] = flow
	api.vpnMutex.Unlock()
	base := "/api/auth-recovery/" + token
	response := apiRequest(handler, "POST", base+"/sms/send", `{"captcha_code":"wrong"}`, nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"captcha_invalid":true`) || !strings.Contains(response.Body.String(), "data:image/png;base64,bmV3") || strings.Contains(response.Body.String(), "private-flow") || strings.Contains(response.Body.String(), "must-not-expose") {
		t.Fatal(response.Code, response.Body.String())
	}
	response = apiRequest(handler, "GET", base+"/status", "", nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"status":"sms_required"`) || !strings.Contains(response.Body.String(), "data:image/png;base64,bmV3") || client.sends != 1 {
		t.Fatal(response.Code, response.Body.String(), client.sends)
	}
	if flow.identifier != "refreshed-private-flow" {
		t.Fatal("candidate identity lost")
	}
}

func TestRemoteRecoveryTokenDoesNotGrantBrowserSession(t *testing.T) {
	api, handler, session, token, client := remoteRecoveryFixture(t)
	base := "/api/auth-recovery/" + token
	for _, action := range []struct{ method, path string }{{"GET", "/status"}, {"POST", "/start"}, {"GET", "/status"}, {"GET", "/poll"}} {
		response := apiRequest(handler, action.method, base+action.path, "", nil)
		if response.Code != 200 || len(response.Result().Cookies()) != 0 {
			t.Fatal("recovery failed or issued browser identity", response.Code)
		}
		if strings.Contains(response.Body.String(), session.Account) || strings.Contains(response.Body.String(), "flow_id") {
			t.Fatal("recovery response exposed account or internal flow identity")
		}
	}
	if client.polls != 1 || api.accounts.Runtime(session).Client != client {
		t.Fatal("recovery did not install authenticated candidate")
	}
	if response := apiRequest(handler, "GET", base+"/status", "", nil); response.Code != 404 {
		t.Fatal("consumed link remained available")
	}
	for _, path := range []string{"/api/status", "/api/documents/gpa/plan", base + "/accounts", "/api/admin/accounts"} {
		if response := apiRequest(handler, "GET", path, "", nil); response.Code != 401 {
			t.Fatal("token route bypass escaped its scope", path)
		}
	}
}

func TestRemoteRecoveryContinuesPendingSMSWithoutSendingAutomatically(t *testing.T) {
	api, handler, session, token, client := remoteRecoveryFixture(t)
	ctx, cancel := context.WithCancel(api.accounts.context)
	defer cancel()
	flow := &browserVPNFlow{client: client, context: ctx, cancel: cancel, created: time.Now(), identifier: "pending-flow", target: "primary", remember: true, bound: session, original: api.accounts.Runtime(session), state: map[string]any{"status": "sms_required", "expires_at": time.Now().Add(5 * time.Minute).Unix(), "captcha_image": "synthetic-captcha"}}
	api.vpnMutex.Lock()
	api.pendingVPN[session.Account] = flow
	api.vpnMutex.Unlock()
	base := "/api/auth-recovery/" + token
	for _, action := range []struct{ method, path string }{{"GET", "/status"}, {"POST", "/start"}} {
		response := apiRequest(handler, action.method, base+action.path, "", nil)
		if response.Code != 200 || !strings.Contains(response.Body.String(), "synthetic-captcha") || client.sends != 0 {
			t.Fatal("pending challenge was replaced or SMS sent automatically")
		}
	}
	if response := apiRequest(handler, "POST", base+"/sms/send", `{"captcha_code":"test"}`, nil); response.Code != 200 || client.sends != 1 {
		t.Fatal("explicit SMS action failed")
	}
	if response := apiRequest(handler, "POST", base+"/sms/verify", `{"code":"test"}`, nil); response.Code != 200 || client.verifies != 1 || len(response.Result().Cookies()) != 0 {
		t.Fatal("SMS recovery failed or issued browser identity")
	}
}

func TestRemoteRecoveryRejectsLateResultAfterStop(t *testing.T) {
	api, handler, session, token, client := remoteRecoveryFixture(t)
	client.pollStarted, client.pollRelease = make(chan struct{}), make(chan struct{})
	base := "/api/auth-recovery/" + token
	if response := apiRequest(handler, "POST", base+"/start", "", nil); response.Code != 200 {
		t.Fatal(response.Code)
	}
	finished := make(chan int, 1)
	go func() { finished <- apiRequest(handler, "GET", base+"/poll", "", nil).Code }()
	<-client.pollStarted
	if err := api.accounts.Stop(context.Background(), session.Account, false); err != nil {
		t.Fatal(err)
	}
	close(client.pollRelease)
	if status := <-finished; status != 404 || api.accounts.Runtime(session) != nil {
		t.Fatal("late authentication resurrected revoked account", status)
	}
}

func TestRemoteRecoveryCancelledAndWrongAccount(t *testing.T) {
	for _, wrongAccount := range []bool{false, true} {
		t.Run(map[bool]string{false: "cancel", true: "wrong-account"}[wrongAccount], func(t *testing.T) {
			api, handler, session, token, client := remoteRecoveryFixture(t)
			original := api.accounts.Runtime(session)
			base := "/api/auth-recovery/" + token
			if response := apiRequest(handler, "POST", base+"/start", "", nil); response.Code != 200 {
				t.Fatal(response.Code)
			}
			if wrongAccount {
				client.account = "20990002"
				if response := apiRequest(handler, "GET", base+"/poll", "", nil); response.Code != 409 {
					t.Fatal("wrong account accepted")
				}
			} else {
				if response := apiRequest(handler, "POST", base+"/cancel", "", nil); response.Code != 200 {
					t.Fatal("cancel failed")
				}
				if response := apiRequest(handler, "POST", base+"/start", "", nil); response.Code != 404 {
					t.Fatal("cancelled link restarted")
				}
			}
			if api.accounts.Runtime(session) != original {
				t.Fatal("original session replaced")
			}
		})
	}
}

func TestRemoteRecoveryNewLoginInvalidatesIssuedLink(t *testing.T) {
	api, handler, session, token, _ := remoteRecoveryFixture(t)
	base := "/api/auth-recovery/" + token
	if response := apiRequest(handler, "POST", base+"/start", "", nil); response.Code != 200 {
		t.Fatal(response.Code)
	}
	if err := api.accounts.Login(context.Background(), session.Account, "test-only", true); err != nil {
		t.Fatal(err)
	}
	if response := apiRequest(handler, "GET", base+"/poll", "", nil); response.Code != 404 {
		t.Fatal("foreground login did not revoke old recovery link")
	}
}

func TestRemoteRecoveryServiceMergesWithoutReplacingPrimary(t *testing.T) {
	api, handler, session, _, candidate := remoteRecoveryFixture(t)
	primary := &testGatewayClient{testCampusClient: testCampusClient{account: session.Account}}
	primary.active.Store(true)
	api.accounts.Runtime(session).Client = primary
	ctx := context.Background()
	settings, _, err := api.readRecoverySettings(ctx, session)
	if err != nil {
		t.Fatal(err)
	}
	recovery, err := api.database.CreateRecoveryContext(ctx, session, "jwxk", "course_selection", "task", settings.Epoch, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	token, err := api.database.RecoveryToken(ctx, recovery.ID)
	if err != nil {
		t.Fatal(err)
	}
	base := "/api/auth-recovery/" + token
	if response := apiRequest(handler, "POST", base+"/start", "", nil); response.Code != 200 {
		t.Fatal(response.Code)
	}
	if response := apiRequest(handler, "GET", base+"/poll", "", nil); response.Code != 200 || len(response.Result().Cookies()) != 0 {
		t.Fatal("service recovery failed or granted browser identity", response.Code)
	}
	if primary.merges != 1 || candidate.target != "jwxk" || api.accounts.Runtime(session).Client != primary {
		t.Fatal("service recovery replaced primary runtime")
	}
}
