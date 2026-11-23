package application

import (
	"context"
	"net/url"
	"strings"
	"testing"
)

type examDeliveryClient struct {
	testCampusClient
	beforeReturn func(string)
}

func (client *examDeliveryClient) Request(_ context.Context, _ string, target string, _ url.Values) ([]byte, error) {
	client.beforeReturn(target)
	if strings.HasSuffix(target, "xnxq.do") {
		return []byte(`{"code":"0","datas":[{"itemCode":"2026-1","itemName":"fixture-term"}]}`), nil
	}
	return []byte(`{"code":"0","datas":[{"courseName":"fixture-exam","examTimeDescription":"2026年6月1日 08:00-10:00"}]}`), nil
}

func TestExamDeliveryAfterSessionRevocation(t *testing.T) {
	for _, endpoint := range []string{"/api/exams/terms", "/api/exams", "/api/exams/export-ics"} {
		t.Run(endpoint, func(t *testing.T) {
			api, handler := testApplication(t, "remote-multi")
			ctx := context.Background()
			client := &examDeliveryClient{testCampusClient: testCampusClient{account: "20250001"}}
			if err := api.database.AddAccount(ctx, client.account); err != nil {
				t.Fatal(err)
			}
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			cookie := loginCookie(t, handler, client.account)
			client.beforeReturn = func(string) {}
			baseline := apiRequest(handler, "GET", endpoint, "", cookie)
			if baseline.Code != 200 || !strings.Contains(baseline.Body.String(), "fixture-") {
				t.Fatal("expected authenticated exam data", baseline.Code)
			}
			revoked := false
			client.beforeReturn = func(target string) {
				if endpoint == "/api/exams/terms" || strings.HasSuffix(target, "exams.do") {
					if err := api.database.RevokeSession(ctx, cookie.Value); err != nil {
						t.Fatal(err)
					}
					revoked = true
				}
			}
			response := apiRequest(handler, "GET", endpoint, "", cookie)
			if !revoked || response.Code < 400 || response.Header().Get("Content-Disposition") != "" || strings.Contains(response.Body.String(), "fixture-") || strings.Contains(response.Body.String(), "BEGIN:VCALENDAR") {
				t.Fatal("unexpected exam delivery", response.Code)
			}
		})
	}
}
