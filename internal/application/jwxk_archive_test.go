package application

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestJWXKArchiveHistoryIsolationDeletionAndLateMerge(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	api.accounts.factory = func(account string) (CampusClient, error) {
		return &jwxkCatalogClient{jwxkSelectedClient: jwxkSelectedClient{testCampusClient: testCampusClient{account: account}}}, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/search", `{"batch_code":"round"}`, cookieA)
	if response.Code != 200 {
		t.Fatal(response)
	}
	waitJWXKScan(t, api, response.Body.String())
	id := jwxkArchiveID("round")
	endpoint := "/api/course-selection/jwxk/catalog/archives/" + id
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"class_id":"class"`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 404 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/search", `{"batch_code":"round"}`, cookieB)
	if response.Code != 200 {
		t.Fatal(response)
	}
	waitJWXKScan(t, api, response.Body.String())
	secondArchive := apiRequest(handler, "GET", endpoint, "", cookieB)
	if secondArchive.Code != 200 || !strings.Contains(secondArchive.Body.String(), `"class_id":"class"`) {
		t.Fatal(secondArchive)
	}
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	document, archive, err := api.readJWXKArchive(ctx, session, "round")
	if err != nil {
		t.Fatal(err)
	}
	if err := api.syncJWXKEligibilitySnapshot(ctx, session, "round", map[string]any{"results": []map[string]any{{"class_id": "class", "status": "selectable", "reason": ""}}}); err != nil {
		t.Fatal(err)
	}
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/catalog/archives", "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"selectable_count":1`) {
		t.Fatal("eligibility not reflected in archive summary", response)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/search", `{"batch_code":"round","local_only":true,"keyword":"not-present"}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"cache_hit":true`) || !strings.Contains(response.Body.String(), `"total":0`) {
		t.Fatal("new query did not use local union", response)
	}
	document, archive, err = api.readJWXKArchive(ctx, session, "round")
	if err != nil {
		t.Fatal(err)
	}
	response = apiRequest(handler, "DELETE", endpoint, "", cookieA)
	if response.Code != 200 {
		t.Fatal(response)
	}
	if err := api.mergeJWXKArchive(ctx, session, "round", document, archive, map[string]any{}); !errors.Is(err, store.ErrConflict) {
		t.Fatal("late refresh resurrected archive", err)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/search", `{"batch_code":"round"}`, cookieA)
	if response.Code != 200 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 404 {
		t.Fatal("subsequent query resurrected deleted archive", response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 200 || response.Body.String() != secondArchive.Body.String() {
		t.Fatal("deletion changed the other account's archive", response)
	}
}
