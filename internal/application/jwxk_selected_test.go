package application

import (
	"context"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type jwxkSelectedClient struct {
	testCampusClient
	calls int
	mode  string
}

func (client *jwxkSelectedClient) RequestService(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
	client.calls++
	client.mode = input.Mode
	if input.Path == "/xsxk/elective/user" {
		return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","typeCode":"02"}]}}}`), nil
	}
	if input.Path == "/xsxk/elective/select" {
		return []byte(`{"code":200,"data":[{"KCH":"course","JXBID":"class","teachingClassType":"FANKC","KRL":10,"YXRS":2,"secretVal":"private"}]}`), nil
	}
	if input.Path == "/xsxk/elective/clazz/list" {
		return []byte(`{"code":200,"data":{"total":1,"rows":[{"KCH":"course","JXBID":"class","KRL":10,"YXRS":4}]}}`), nil
	}
	return []byte(`{"code":200,"data":[]}`), nil
}

func TestJWXKSelectedHTTPAuthRouteAndMarket(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/selected"
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, nil); response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", endpoint+"?include_market=invalid", `{"batch_code":"round"}`, cookie); response.Code != 422 || client.calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "PUT", "/api/course-selection/jwxk/settings?probe=false", `{"network_mode":"webvpn"}`, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "POST", endpoint+"?include_market=false", `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || client.calls != 5 || client.mode != "webvpn" || response.Header().Get("Cache-Control") != "no-store" || strings.Contains(response.Body.String(), "private") || !strings.Contains(response.Body.String(), `"selected_count":2`) {
		t.Fatal(response, client.calls)
	}
	response = apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || client.calls != 12 || !strings.Contains(response.Body.String(), `"selected_count":4`) || !strings.Contains(response.Body.String(), `"market_complete":true`) {
		t.Fatal(response, client.calls)
	}
}
