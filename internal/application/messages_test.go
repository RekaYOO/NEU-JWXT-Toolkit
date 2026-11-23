package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type messagesTestClient struct {
	testCampusClient
	calls int
}

func (client *messagesTestClient) Request(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
	client.calls++
	if form.Get("WID") == "uncertain" {
		return nil, errors.New("lost response")
	}
	return []byte(`{"code":"0"}`), nil
}

func TestMessageAcknowledgementDoesNotReplayAndPreservesUnconfirmed(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	client := &messagesTestClient{testCampusClient: testCampusClient{account: "20250001"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "20250001")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	entry := store.CacheEntry{Resource: "system-messages", Variant: "default", SchemaVersion: 1, Revision: "old", Payload: json.RawMessage(`{"messages":[{"id":"confirmed","kind":"reminder","read":false},{"id":"uncertain","kind":"audit","read":false}]}`)}
	if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
		t.Fatal(err)
	}
	before, _ := api.database.ReadCache(context.Background(), session, "system-messages", "default")
	response := apiRequest(handler, "POST", "/api/system-messages/read", `{"messages":[{"message_id":"confirmed"},{"message_id":"confirmed"},{"message_id":"uncertain","kind":"audit"}]}`, cookie)
	if response.Code != 200 || client.calls != 2 {
		t.Fatalf("replayed or failed: %d %s calls=%d", response.Code, response.Body.String(), client.calls)
	}
	after, err := api.database.ReadCache(context.Background(), session, "system-messages", "default")
	if err != nil || after.CheckedAt != before.CheckedAt {
		t.Fatal("acknowledgement incorrectly renewed list freshness")
	}
	var payload struct {
		Messages []struct {
			ID   string `json:"id"`
			Read bool   `json:"read"`
		} `json:"messages"`
	}
	if json.Unmarshal(after.Payload, &payload) != nil || !payload.Messages[0].Read || payload.Messages[1].Read {
		t.Fatalf("unconfirmed message changed: %s", after.Payload)
	}
}
