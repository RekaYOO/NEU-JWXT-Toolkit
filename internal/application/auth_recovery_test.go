package application

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type recoveryTestClient struct {
	testVPNClient
	mode                  string
	probe                 func(context.Context) error
	directError, vpnError error
	vpnStatus             string
	probes, directs, vpns atomic.Int32
}

func (client *recoveryTestClient) Restore(cookies []campus.SavedCookie, mode string) {
	client.mode = mode
}
func (client *recoveryTestClient) Status() (bool, string)                   { return client.active.Load(), client.mode }
func (client *recoveryTestClient) Snapshot() ([]campus.SavedCookie, string) { return nil, client.mode }
func (client *recoveryTestClient) VerifySaved(ctx context.Context) error {
	client.probes.Add(1)
	err := client.probe(ctx)
	client.active.Store(err == nil)
	return err
}
func (client *recoveryTestClient) LoginDirect(context.Context, string) error {
	client.directs.Add(1)
	client.mode = "direct"
	client.active.Store(client.directError == nil)
	return client.directError
}
func (client *recoveryTestClient) StartVPNPassword(ctx context.Context, password string) (map[string]any, error) {
	client.vpns.Add(1)
	client.mode = "webvpn"
	if client.vpnError != nil {
		return nil, client.vpnError
	}
	if client.vpnStatus == "sms_required" {
		return client.testVPNClient.StartVPNPassword(ctx, password)
	}
	client.active.Store(true)
	return map[string]any{"status": "authenticated", "username": client.account}, nil
}

func setupAuthRecovery(t *testing.T, mode, password string) (*API, http.Handler, *http.Cookie, store.Session, *Runtime) {
	t.Helper()
	api, handler := testApplication(t, "remote-multi")
	api.accounts.mutex.Lock()
	api.accounts.automationTick, api.accounts.mailTick = nil, nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	api.database.AddAccount(ctx, "student")
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(ctx, cookie.Value)
	original := api.accounts.Runtime(session)
	original.Client.(*testCampusClient).active.Store(false)
	payload, _ := json.Marshal(savedCampusSession{Account: "student", Password: password, Mode: mode})
	if err := api.database.SaveSecretForSession(ctx, session, "campus-session", payload); err != nil {
		t.Fatal(err)
	}
	return api, handler, cookie, session, original
}

func waitRecovery(t *testing.T, api *API, session store.Session) {
	t.Helper()
	api.recoveryMutex.Lock()
	identifier := api.recoveries[session.Account].jobID
	api.recoveryMutex.Unlock()
	job, exists := api.resources.Job(session, identifier)
	if !exists {
		t.Fatal("recovery job missing")
	}
	select {
	case <-job.done:
	case <-time.After(3 * time.Second):
		t.Fatal("recovery job did not finish")
	}
}

func TestRecoveryProbeFailureDoesNotSendSavedPassword(t *testing.T) {
	for code, status := range map[string]string{"UPSTREAM_UNAVAILABLE": "retry_wait", "WEBVPN_UPSTREAM_TIMEOUT": "retry_wait", "UPSTREAM_RESPONSE_INVALID": "retry_wait", "AUTH_ACCOUNT_MISMATCH": "manual_required"} {
		t.Run(code, func(t *testing.T) {
			api, _, _, session, original := setupAuthRecovery(t, "direct", "saved-password")
			client := &recoveryTestClient{probe: func(context.Context) error { return &campus.Error{Code: code} }}
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			api.scheduleAuthRecovery(session)
			waitRecovery(t, api, session)
			if client.probes.Load() != 1 || client.directs.Load() != 0 || client.vpns.Load() != 0 || api.accounts.Runtime(session) != original || api.authRecoverySnapshot(session)["status"] != status {
				t.Fatal("unexpected password recovery after probe failure", api.authRecoverySnapshot(session))
			}
		})
	}
}

func TestRecoveryCookieProbeDoesNotResubmitPassword(t *testing.T) {
	api, handler, cookie, session, original := setupAuthRecovery(t, "webvpn", "saved-password")
	config := json.RawMessage(`{"enabled":true,"interval_minutes":30,"start_hour":9,"end_hour":21}`)
	api.database.SaveForSession(context.Background(), session, gradeTrackingNamespace, "config", config, 0, false)
	statePayload, _ := json.Marshal(map[string]any{"generation": session.Generation, "activation_id": "existing", "activation_queued": "existing", "stage": "waiting_login", "next_check_at": time.Now().Add(time.Hour).UTC().Format(time.RFC3339Nano)})
	api.database.SaveForSession(context.Background(), session, gradeTrackingNamespace, "state", statePayload, 0, false)
	client := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, probe: func(context.Context) error { return nil }}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	if client.probes.Load() != 1 || client.directs.Load() != 0 || client.vpns.Load() != 0 {
		t.Fatal("cookie restoration resubmitted credentials")
	}
	if api.accounts.Runtime(session) == original || original.Context.Err() == nil {
		t.Fatal("old runtime not replaced")
	}
	response := apiRequest(handler, "GET", "/api/status", "", cookie)
	if !strings.Contains(response.Body.String(), `"is_logged_in":true`) || !strings.Contains(response.Body.String(), `"status":"authenticated"`) {
		t.Fatal(response)
	}
	before := client.probes.Load()
	apiRequest(handler, "GET", "/api/status", "", cookie)
	if client.probes.Load() != before {
		t.Fatal("status query accessed school")
	}
	state, _, err := api.readTrackingState(context.Background(), session)
	next, parseError := time.Parse(time.RFC3339Nano, state["next_check_at"].(string))
	if err != nil || parseError != nil || next.After(time.Now()) || state["stage"] != "scheduled" || state["activation_queued"] != "existing" {
		t.Fatal("tracking not resumed with existing activation", state, err, parseError)
	}
}

func TestRecoverySingleFlightBackoffAndNoPasswordReplay(t *testing.T) {
	api, _, _, session, _ := setupAuthRecovery(t, "direct", "saved-password")
	entered, release := make(chan struct{}), make(chan struct{})
	client := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, directError: &campus.Error{Code: "UPSTREAM_UNAVAILABLE", Message: "uncertain password response"}}
	client.probe = func(context.Context) error {
		close(entered)
		<-release
		return &campus.Error{Code: "AUTH_REQUIRED", Message: "expired"}
	}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	api.scheduleAuthRecovery(session)
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("recovery did not begin")
	}
	for count := 0; count < 10; count++ {
		api.scheduleAuthRecovery(session)
	}
	close(release)
	waitRecovery(t, api, session)
	if client.probes.Load() != 1 || client.directs.Load() != 1 || client.vpns.Load() != 0 {
		t.Fatal("duplicate recovery or uncertain password replay")
	}
	snapshot := api.authRecoverySnapshot(session)
	if snapshot["status"] != "retry_wait" || snapshot["retry_after_seconds"].(int) > 5 || snapshot["retry_after_seconds"].(int) < 3 {
		t.Fatal(snapshot)
	}
	api.scheduleAuthRecovery(session)
	if client.probes.Load() != 1 {
		t.Fatal("backoff ignored")
	}
	client.probe = func(context.Context) error { return &campus.Error{Code: "AUTH_REQUIRED", Message: "expired"} }
	api.recoveryMutex.Lock()
	api.recoveries[session.Account].next = time.Now().Add(-time.Second)
	api.recoveryMutex.Unlock()
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	snapshot = api.authRecoverySnapshot(session)
	if client.probes.Load() != 2 || snapshot["retry_after_seconds"].(int) < 8 || snapshot["retry_after_seconds"].(int) > 10 {
		t.Fatal(snapshot)
	}
	for attempt := 0; attempt < 5; attempt++ {
		api.recoveryMutex.Lock()
		api.recoveries[session.Account].next = time.Now().Add(-time.Second)
		api.recoveryMutex.Unlock()
		api.scheduleAuthRecovery(session)
		waitRecovery(t, api, session)
	}
	snapshot = api.authRecoverySnapshot(session)
	if delay := snapshot["retry_after_seconds"].(int); delay < 118 || delay > 120 {
		t.Fatal("backoff not capped at two minutes", snapshot)
	}
}

func TestRecoveryCredentialRejectionNeverSwitchesRoute(t *testing.T) {
	api, _, _, session, _ := setupAuthRecovery(t, "direct", "saved-password")
	client := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, probe: func(context.Context) error { return &campus.Error{Code: "AUTH_REQUIRED", Message: "expired"} }, directError: &campus.Error{Code: "WRONG_PASSWORD", Message: "wrong"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	if snapshot := api.authRecoverySnapshot(session); snapshot["status"] != "credentials_invalid" {
		t.Fatal(snapshot)
	}
	api.scheduleAuthRecovery(session)
	if client.directs.Load() != 1 || client.vpns.Load() != 0 {
		t.Fatal("rejected password retried or changed route")
	}
}

func TestRecoveryDirectFailureContinuesSameSMSCandidateInFrontend(t *testing.T) {
	api, handler, cookie, session, _ := setupAuthRecovery(t, "direct", "saved-password")
	client := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, probe: func(context.Context) error { return &campus.Error{Code: "AUTH_REQUIRED", Message: "expired"} }, directError: &campus.Error{Code: "DIRECT_ACCESS_FAILED", Message: "unreachable"}, vpnStatus: "sms_required"}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	api.scheduleAuthRecovery(session)
	if client.directs.Load() != 1 || client.vpns.Load() != 1 || client.sends != 0 {
		t.Fatal("background sent SMS or repeated password")
	}
	response := apiRequest(handler, "GET", "/api/auth/pending", "", cookie)
	if !strings.Contains(response.Body.String(), `"required":true`) || strings.Contains(response.Body.String(), "saved-password") {
		t.Fatal(response)
	}
	api.database.AddAccount(context.Background(), "other")
	otherToken, _ := api.database.CreateSession(context.Background(), "other", time.Hour)
	otherCookie := &http.Cookie{Name: sessionCookie, Value: otherToken}
	response = apiRequest(handler, "GET", "/api/auth/pending", "", otherCookie)
	if strings.Contains(response.Body.String(), "test-flow") {
		t.Fatal("other account saw pending challenge")
	}
	response = apiRequest(handler, "POST", "/api/webvpn/sms/send", `{"flow_id":"test-flow","captcha_code":"manual"}`, otherCookie)
	if client.sends != 0 {
		t.Fatal("other account sent SMS", response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/sms/send", `{"flow_id":"test-flow","captcha_code":"manual"}`, cookie)
	if client.sends != 1 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/webvpn/sms/verify", `{"flow_id":"test-flow","code":"manual"}`, cookie)
	if !strings.Contains(response.Body.String(), `"status":"authenticated"`) || client.verifies != 1 || client.vpns.Load() != 1 {
		t.Fatal(response)
	}
	responseCookie(t, response.Result(), sessionCookie)
}

func TestRecoveryLateSuccessCannotUndoLogout(t *testing.T) {
	api, _, _, session, _ := setupAuthRecovery(t, "direct", "saved-password")
	entered, release := make(chan struct{}), make(chan struct{})
	client := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, probe: func(context.Context) error { close(entered); <-release; return nil }}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	api.scheduleAuthRecovery(session)
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("recovery did not begin")
	}
	if err := api.accounts.Stop(context.Background(), session.Account, true); err != nil {
		t.Fatal(err)
	}
	close(release)
	waitRecovery(t, api, session)
	if api.accounts.Runtime(session) != nil || client.active.Load() {
		t.Fatal("late recovery revived logged-out account")
	}
	if _, err := api.database.ReadSecret(context.Background(), session.Account, "campus-session"); err == nil {
		t.Fatal("late recovery restored credentials")
	}
}

type memoryCookieClient struct{ testCampusClient }

func (client *memoryCookieClient) Snapshot() ([]campus.SavedCookie, string) {
	return []campus.SavedCookie{{URL: "https://jwxt.neu.edu.cn/", Cookie: http.Cookie{Name: "session", Value: "test-only", Path: "/"}}}, "direct"
}

func TestRecoveryWithoutRememberUsesOnlyMemoryCookies(t *testing.T) {
	api, _, _, session, original := setupAuthRecovery(t, "direct", "")
	api.database.DeleteSecret(context.Background(), session.Account, "campus-session")
	original.Client = &memoryCookieClient{testCampusClient: testCampusClient{account: "student"}}
	candidate := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, probe: func(context.Context) error { return nil }}
	api.accounts.factory = func(string) (CampusClient, error) { return candidate, nil }
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	if api.accounts.Runtime(session) == original || candidate.probes.Load() != 1 || candidate.directs.Load() != 0 {
		t.Fatal("memory cookie recovery failed")
	}
	if _, err := api.database.ReadSecret(context.Background(), session.Account, "campus-session"); err == nil {
		t.Fatal("recovery persisted unremembered session")
	}
}
