package application

import (
	"context"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type jwxkEligibilityClient struct{ jwxkSelectedClient }

func (client *jwxkEligibilityClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if input.Path == "/xsxk/elective/check" {
		client.calls++
		client.mode = input.Mode
		return []byte(`{"code":200,"data":["是否可选：不可选","学生：private-student","先修课程未完成"]}`), nil
	}
	return client.jwxkSelectedClient.RequestService(ctx, input)
}

func TestJWXKEligibilityHTTPBoundary(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkEligibilityClient{jwxkSelectedClient: jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/catalog/eligibility"
	body := `{"batch_code":"round","class_ids":["class","class"]}`
	if response := apiRequest(handler, "POST", endpoint, body, nil); response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round","class_ids":[]}`, cookie); response.Code != 422 || client.calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "PUT", "/api/course-selection/jwxk/settings?probe=false", `{"network_mode":"webvpn"}`, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || client.calls != 2 || client.mode != "webvpn" || response.Header().Get("Cache-Control") != "no-store" || strings.Contains(response.Body.String(), "private-student") || !strings.Contains(response.Body.String(), `"status":"unavailable"`) || !strings.Contains(response.Body.String(), "先修课程未完成") {
		t.Fatal(response, client.calls)
	}
}
