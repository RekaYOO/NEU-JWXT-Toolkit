package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestJWXKStatusPersistsRoundForLocalPlanBootstrap(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	clients := map[string]*jwxkCatalogClient{}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkCatalogClient{jwxkSelectedClient: jwxkSelectedClient{testCampusClient: testCampusClient{account: account}}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	response := apiRequest(handler, "GET", "/api/course-selection/jwxk/status", "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"service_authenticated":true`) {
		t.Fatal(response)
	}
	before := clients["student-a"].calls
	clients["student-a"].Close()
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/plan/read", `{"batch_code":"round"}`, cookieA)
	if response.Code != 200 || clients["student-a"].calls != before || !strings.Contains(response.Body.String(), `"selection_type_code":"02"`) || !strings.Contains(response.Body.String(), `"snapshot":true`) || !strings.Contains(response.Body.String(), `"can_enter":false`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/plan/read", `{"batch_code":"round"}`, cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"batch":null`) || clients["student-b"].calls != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "PUT", "/api/documents/jwxk-rounds/round", `{"revision":1,"data":{"can_enter":true}}`, cookieA)
	if response.Code != 403 {
		t.Fatal("client forged official snapshot", response)
	}
}

func TestJWXKRoundSnapshotProjectsStaticMetadataAndKeepsDeletion(t *testing.T) {
	api, _ := testApplication(t, "remote-multi")
	ctx := context.Background()
	if err := api.database.AddAccount(ctx, "student-a"); err != nil {
		t.Fatal(err)
	}
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	payload := map[string]any{"batches": []map[string]any{{"code": "round", "name": "轮次", "term_code": "term", "selection_type_code": "04", "state": "active", "can_enter": true, "secretVal": "private"}}}
	if err := api.saveJWXKRounds(ctx, session, payload); err != nil {
		t.Fatal(err)
	}
	snapshot, err := api.readJWXKRound(ctx, session, "round")
	if err != nil || snapshot["term_code"] != "term" || snapshot["state"] != "unknown" || snapshot["can_enter"] != false {
		t.Fatal(snapshot, err)
	}
	encoded, _ := json.Marshal(snapshot)
	if strings.Contains(string(encoded), "private") {
		t.Fatal("private field persisted")
	}
	if _, err := api.database.SaveForSession(ctx, session, jwxkRoundNamespace, "round", []byte("null"), 1, true); err != nil {
		t.Fatal(err)
	}
	if err := api.saveJWXKRounds(ctx, session, payload); err != nil {
		t.Fatal(err)
	}
	if snapshot, err := api.readJWXKRound(ctx, session, "round"); err != nil || snapshot != nil {
		t.Fatal("deleted snapshot recreated", snapshot, err)
	}
}
