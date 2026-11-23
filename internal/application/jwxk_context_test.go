package application

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type jwxkContextClient struct {
	testCampusClient
	calls  int
	failed bool
	mode   string
}

func (client *jwxkContextClient) RequestService(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
	client.calls++
	client.mode = input.Mode
	if client.failed {
		return nil, &campus.Error{Code: "JWXK_NOT_IN_SELECTION_ROUND", Message: "当前不在选课轮次中"}
	}
	if strings.HasSuffix(input.Path, "/now") {
		return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
	}
	return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[],"expElectiveBatchList":[]}}}`), nil
}

func TestJWXKPassiveStatusSettingsAndAccountIsolation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(context.Background(), account); err != nil {
			t.Fatal(err)
		}
	}
	clients := map[string]*jwxkContextClient{}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkContextClient{testCampusClient: testCampusClient{account: account}}
		clients[account] = client
		return client, nil
	}
	cookieA := loginCookie(t, handler, "student-a")
	cookieB := loginCookie(t, handler, "student-b")
	response := apiRequest(handler, "PUT", "/api/course-selection/jwxk/settings?probe=false", `{"network_mode":"webvpn"}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"effective_network_mode":"webvpn"`) || clients["student-a"].calls != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/status?probe=false", "", cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"network_mode":"follow"`) || clients["student-b"].calls != 0 {
		t.Fatal("other account inherited route")
	}
	session, err := api.database.Session(context.Background(), cookieA.Value)
	if err != nil {
		t.Fatal(err)
	}
	api.pendingVPN[session.Account] = &browserVPNFlow{bound: session, target: "jwxk", context: context.Background(), created: time.Now(), state: map[string]any{"status": "sms_required", "captcha_image": "fixture-private-image"}}
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/status", "", cookieA)
	delete(api.pendingVPN, session.Account)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"service_auth_state":"interaction_required"`) || strings.Contains(response.Body.String(), "fixture-private-image") || clients["student-a"].calls != 0 {
		t.Fatal("pending challenge was not isolated from school probe", response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/status", "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"service_authenticated":true`) || clients["student-a"].mode != "webvpn" {
		t.Fatal(response)
	}
	clients["student-a"].failed = true
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/status", "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"service_auth_state":"not_in_selection_round"`) || !strings.Contains(response.Body.String(), `"network_mode":"webvpn"`) {
		t.Fatal(response)
	}
	if active, _ := clients["student-a"].Status(); !active {
		t.Fatal("service failure logged out primary")
	}
	before := clients["student-a"].calls
	if response := apiRequest(handler, "GET", "/api/course-selection/jwxk/status?probe=0", "", cookieA); response.Code != 200 || clients["student-a"].calls != before {
		t.Fatal("disabled probe contacted school")
	}
	if response := apiRequest(handler, "PUT", "/api/course-selection/jwxk/settings?probe=invalid", `{"network_mode":"direct"}`, cookieA); response.Code != 422 {
		t.Fatal("invalid probe accepted")
	}
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/status?probe=false", "", cookieA)
	if !strings.Contains(response.Body.String(), `"network_mode":"webvpn"`) {
		t.Fatal("invalid request changed saved preference")
	}
	if response := apiRequest(handler, "GET", "/api/course-selection/jwxk/status?probe=false", "", nil); response.Code != 401 {
		t.Fatal("anonymous account settings exposed")
	}
}
