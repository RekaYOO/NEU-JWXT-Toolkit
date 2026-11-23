package application

import (
	"context"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestAutomationSettingsAreOfflineAccountAndRoundScoped(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	clients := map[string]*jwxkSelectedClient{}
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(ctx, account)
	}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: account}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	clients["student-a"].Close()
	endpoint := "/api/course-selection/jwxk/batches/round/automation-settings"
	response := apiRequest(handler, "GET", endpoint, "", nil)
	if response.Code != 401 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"rebalance_seconds":1800`) || !strings.Contains(response.Body.String(), `"smtp_configured":false`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "PUT", endpoint, `{"strategy_schedule_mode":"final_windows","rebalance_seconds":600,"mail_enabled":true,"notify_round_end":true,"over_capacity_ratio":0.5}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"notify_round_end":true`) || !strings.Contains(response.Body.String(), `"strategy_schedule_mode":"final_windows"`) {
		t.Fatal(response)
	}
	fresh := NewAPI(api.database, api.accounts, "test")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	settings, _, err := fresh.readJWXKAutomationSettings(ctx, session, "round")
	if err != nil || !settings.NotifyRoundEnd || settings.RebalanceSeconds != 600 {
		t.Fatal(settings, err)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"mail_enabled":false`) {
		t.Fatal("other account settings leaked", response)
	}
	response = apiRequest(handler, "GET", strings.Replace(endpoint, "/round/", "/another/", 1), "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"mail_enabled":false`) {
		t.Fatal("other round settings leaked", response)
	}
	for _, payload := range []string{`{"smtp_configured":true}`, `{"rebalance_seconds":30}`, `{"final_notice_latest_time":"invalid"}`, `{"mail_enabled":null}`} {
		response = apiRequest(handler, "PUT", endpoint, payload, cookieA)
		if response.Code != 422 {
			t.Fatal(payload, response)
		}
	}
	response = apiRequest(handler, "PUT", "/api/documents/jwxk-automation-settings/round", `{"data":{"rebalance_seconds":1},"revision":1}`, cookieA)
	if response.Code != 403 {
		t.Fatal("validation bypass", response)
	}
	if clients["student-a"].calls != 0 || clients["student-b"].calls != 0 {
		t.Fatal("settings contacted school")
	}
	api.database.SetEnabled(ctx, "student-a", false)
	if _, _, err := fresh.readJWXKAutomationSettings(ctx, session, "round"); err == nil {
		t.Fatal("revoked session read automation settings")
	}
}

func TestAutomationSettingsReadOnlyMetadataWithoutRuntime(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	cookie := loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	api.database.SaveForSession(ctx, session, jwxkRoundNamespace, "round", []byte(`{"code":"round","name":"已结束轮次","term_code":"2026-2027-1","selection_type_code":"04"}`), 0, false)
	api.accounts.Runtime(session).Client.Close()
	response := apiRequest(handler, "GET", "/api/course-selection/jwxk/batches/round/automation-settings", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"batch_name":"已结束轮次"`) || !strings.Contains(response.Body.String(), `"selection_type_code":"04"`) {
		t.Fatal(response)
	}
}
