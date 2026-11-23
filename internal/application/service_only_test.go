package application

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type testScopedClient struct {
	testVPNClient
	requests, verifications int
	verificationError       error
}

func (client *testScopedClient) Restore([]campus.SavedCookie, string) { client.active.Store(false) }
func (client *testScopedClient) VerifyServiceSession(context.Context, string, string) error {
	client.verifications++
	if client.verificationError != nil {
		return client.verificationError
	}
	client.active.Store(true)
	return nil
}
func (client *testScopedClient) RequestService(context.Context, campus.ServiceRequest) ([]byte, error) {
	client.requests++
	return []byte(`{"code":200}`), nil
}

func TestIndependentSelectionRecoveryRestartsWithoutGrantingPrimaryAccess(t *testing.T) {
	api, handler, session, _, _ := remoteRecoveryFixture(t)
	ctx := context.Background()
	api.accounts.mutex.Lock()
	previous := api.accounts.runtimes[session.Account]
	delete(api.accounts.runtimes, session.Account)
	api.accounts.mutex.Unlock()
	previous.Cancel()
	previous.Client.Close()
	if err := api.database.DeleteSecret(ctx, session.Account, "campus-session"); err != nil {
		t.Fatal(err)
	}
	candidate := &testScopedClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: session.Account}}}
	api.vpnFactory = func(string) (VPNClient, error) { return candidate, nil }
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
		t.Fatal(response.Code)
	}
	runtime := api.accounts.Runtime(session)
	if runtime == nil {
		t.Fatal("missing standalone task runtime")
	}
	if active, _ := runtime.Client.Status(); active {
		t.Fatal("service-only identity became primary identity")
	}
	if _, err := api.accounts.CreateSession(ctx, session.Account, time.Hour); err == nil {
		t.Fatal("service-only recovery issued browser session")
	}
	if _, err := runtime.Request(ctx, "GET", "https://jwxt.neu.edu.cn/", nil); err == nil {
		t.Fatal("primary request escaped scoped client")
	}
	for _, request := range []campus.ServiceRequest{{Service: "cxcy", Mode: "webvpn"}, {Service: "jwxk", Mode: "direct"}} {
		if _, err := runtime.RequestService(ctx, request); err == nil {
			t.Fatal("service or route scope escaped")
		}
	}
	if _, err := runtime.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: "webvpn", Method: http.MethodGet, Path: "/xsxk/web/now"}); err != nil || candidate.requests != 1 {
		t.Fatal(err)
	}
	resumed, err := NewAccounts(api.database, "remote-multi")
	if err != nil {
		t.Fatal(err)
	}
	defer resumed.Close()
	restored := &testScopedClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: session.Account}}}
	resumed.factory = func(string) (CampusClient, error) { return restored, nil }
	if err := resumed.ResumeSaved(); err != nil {
		t.Fatal(err)
	}
	newRuntime := resumed.Runtime(session)
	if newRuntime == nil || restored.verifications != 0 {
		t.Fatal("restart should restore unverified scoped cookies only")
	}
	restored.verificationError = errors.New("service unavailable")
	if _, err := newRuntime.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: "webvpn"}); err == nil || restored.requests != 0 {
		t.Fatal("unverified business request sent")
	}
	restored.verificationError = nil
	if _, err := newRuntime.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: "webvpn"}); err != nil || restored.requests != 1 {
		t.Fatal(err)
	}
	if err := resumed.Stop(ctx, session.Account, false); err != nil {
		t.Fatal(err)
	}
	if err := resumed.ResumeSaved(); err != nil {
		t.Fatal(err)
	}
	if len(resumed.runtimes) != 0 {
		t.Fatal("revoked service session restarted")
	}
}

func TestPrimaryLoginPreservesIndependentGatewayAndRemainsDirect(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student")
	loginCookie(t, handler, "student")
	api.accounts.mutex.Lock()
	runtime := api.accounts.runtimes["student"]
	old := runtime.Client
	runtime.Client = &serviceOnlyClient{candidate: old}
	api.accounts.mutex.Unlock()
	primary := &testGatewayClient{testCampusClient: testCampusClient{account: "student"}}
	api.accounts.factory = func(string) (CampusClient, error) { return primary, nil }
	if err := api.accounts.Login(ctx, "student", "test-only", true); err != nil {
		t.Fatal(err)
	}
	if active, mode := primary.Status(); !active || mode != "direct" || primary.merges != 1 {
		t.Fatal("primary login lost gateway or changed primary route")
	}
}

func TestStandaloneSelectionTaskRunsWithoutPrimaryLogin(t *testing.T) {
	candidate := &selectionTaskClient{}
	api, handler, cookie, session, id := selectionTaskSetup(t, candidate)
	if response := apiRequest(handler, "POST", "/api/course-selection/jwxk/automation/tasks/start", `{"task_id":"`+id+`"}`, cookie); response.Code != 200 {
		t.Fatal(response.Code)
	}
	runtime := api.accounts.Runtime(session)
	runtime.Client = &serviceOnlyClient{candidate: candidate}
	api.scheduleAutomationTasks()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		api.resources.mutex.Lock()
		var done <-chan struct{}
		for _, job := range api.resources.jobs {
			if job.Resource == "jwxk-automation" {
				done = job.done
				break
			}
		}
		api.resources.mutex.Unlock()
		if done != nil {
			select {
			case <-done:
				if candidate.writes == 0 {
					t.Fatal("scoped task did not execute")
				}
				return
			case <-time.After(3 * time.Second):
				t.Fatal("task did not finish")
			}
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("scoped task was not scheduled")
}
