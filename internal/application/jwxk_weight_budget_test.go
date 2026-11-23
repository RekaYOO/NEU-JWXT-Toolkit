package application

import (
	"context"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type jwxkBudgetClient struct{ jwxkSelectedClient }

func (client *jwxkBudgetClient) RequestService(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
	client.calls++
	client.mode = input.Mode
	return []byte(`{"code":200,"data":{"remainingWeight":90,"student":{"electiveBatchList":[{"code":"round"}]}}}`), nil
}

func TestJWXKWeightBudgetHTTPRequiresActiveAccount(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkBudgetClient{jwxkSelectedClient: jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/weights/budget"
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, nil); response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"../bad"}`, cookie); response.Code != 422 || client.calls != 0 {
		t.Fatal(response)
	}
	apiRequest(handler, "PUT", "/api/course-selection/jwxk/settings?probe=false", `{"network_mode":"webvpn"}`, cookie)
	response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || client.calls != 1 || client.mode != "webvpn" || response.Header().Get("Cache-Control") != "no-store" || !strings.Contains(response.Body.String(), `"remaining":90`) {
		t.Fatal(response)
	}
	client.Close()
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round"}`, cookie); response.Code != 401 || client.calls != 1 {
		t.Fatal(response)
	}
}
