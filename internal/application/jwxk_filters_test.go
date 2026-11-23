package application

import (
	"context"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestJWXKFiltersUseOnlyCurrentAccountAndBatchSnapshots(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	if err := api.database.AddAccount(ctx, "student-a"); err != nil {
		t.Fatal(err)
	}
	client := &jwxkSelectedClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	for _, batch := range []string{"round", "round-other"} {
		input := academic.JWXKCatalogSearch{Batch: batch, Scope: "ALL", Page: 1, Size: 20, OnlineMode: "all"}
		payload := map[string]any{"scope_options": []any{map[string]string{"code": "FANKC", "name": "方案内课"}}, "groups": []any{map[string]any{"classes": []any{map[string]any{"department": batch, "campus": "00"}}}}}
		if err := api.saveJWXKQuerySnapshot(ctx, session, jwxkQueryKey(input), payload, 0); err != nil {
			t.Fatal(err)
		}
	}
	client.Close()
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/filter-options", `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || client.calls != 0 || !strings.Contains(response.Body.String(), `"value":"round"`) || strings.Contains(response.Body.String(), "round-other") {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/filter-options", `{"batch_code":"round"}`, nil)
	if response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
}
