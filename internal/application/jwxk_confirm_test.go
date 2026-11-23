package application

import (
	"context"
	"testing"
)

func TestJWXKConfirmRequiresSessionAndAcknowledgment(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	endpoint := "/api/course-selection/jwxk/batches/confirm"
	if response := apiRequest(handler, "POST", endpoint, `{"batch_code":"round","acknowledged":true}`, nil); response.Code != 401 {
		t.Fatal(response)
	}
	for _, body := range []string{`{"batch_code":"round"}`, `{"batch_code":"round","acknowledged":false}`, `{"batch_code":"round","acknowledged":"true"}`} {
		response := apiRequest(handler, "POST", endpoint, body, cookie)
		if response.Code != 422 && response.Code != 400 {
			t.Fatal(response)
		}
	}
	if client.calls != 0 {
		t.Fatal("invalid consent contacted school")
	}
}
