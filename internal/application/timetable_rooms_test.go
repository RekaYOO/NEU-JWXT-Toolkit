package application

import (
	"context"
	"net/url"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type roomQueryClient struct {
	testCampusClient
	response string
	failure  error
}

func (client *roomQueryClient) Request(_ context.Context, method, target string, _ url.Values) ([]byte, error) {
	if method == "POST" && strings.HasSuffix(target, "/cxkxjs.do") {
		return []byte(client.response), client.failure
	}
	return []byte(`{}`), nil
}

func TestRoomOfficialRouteFallsBackOnlyForUnavailableData(t *testing.T) {
	for _, scenario := range []struct {
		name, response, query, source string
		failure                       error
		status                        int
	}{
		{name: "valid empty", response: `{"code":"0","datas":{"cxkxjs":{"totalSize":0,"pageSize":15,"rows":[]}}}`, status: 200, source: `"source":"official"`},
		{name: "malformed", response: "<html></html>", status: 200, source: `"source":"schedule"`},
		{name: "timeout", failure: context.DeadlineExceeded, status: 200, source: `"source":"schedule"`},
		{name: "authentication expired", failure: &campus.Error{Code: "AUTH_EXPIRED", Message: "登录过期"}, status: 401},
		{name: "invalid date", query: `{"term_code":"2026-2027-1","date":"2026-02-30","start_section":3,"end_section":4}`, status: 422},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			api, handler := testApplication(t, "local-single")
			client := &roomQueryClient{testCampusClient: testCampusClient{account: "20990001"}, response: scenario.response, failure: scenario.failure}
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			cookie := loginCookie(t, handler, client.account)
			query := scenario.query
			if query == "" {
				query = `{"term_code":"2026-2027-1","date":"2026-09-09","start_section":3,"end_section":4}`
			}
			response := apiRequest(handler, "POST", "/api/timetable/rooms/official-availability", query, cookie)
			if response.Code != scenario.status || !strings.Contains(response.Body.String(), scenario.source) || response.Header().Get("Cache-Control") != "no-store" {
				t.Fatalf("unexpected response: %d %s", response.Code, response.Body.String())
			}
		})
	}
}
