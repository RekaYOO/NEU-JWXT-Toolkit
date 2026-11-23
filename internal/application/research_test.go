package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func commitResearchFixture(t *testing.T, api *API, session store.Session, batch, title string) json.RawMessage {
	t.Helper()
	topics := []map[string]any{}
	if title != "" {
		topics = append(topics, map[string]any{"topic_id": "topic", "batch_id": batch, "title": title, "can_enroll": true})
	}
	payload, _ := json.Marshal(map[string]any{"batch": map[string]any{"batch_id": batch, "name": "批次"}, "eligibility": map[string]any{"available": false}, "topics": topics, "confirmed_topics": []any{}, "total": len(topics)})
	_, err := api.database.CommitCache(context.Background(), session, store.CacheEntry{Resource: "research-training", Variant: "default", SchemaVersion: 1, Payload: payload, Revision: batch + title}, "test", nil)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func researchResponse(t *testing.T, handler http.Handler, cookie *http.Cookie, method, path, body string) map[string]any {
	t.Helper()
	response := apiRequest(handler, method, path, body, cookie)
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func TestResearchConcurrentFavoritesKeepBothTopics(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	payload := json.RawMessage(`{"batch":{"batch_id":"batch"},"topics":[{"topic_id":"first","title":"First"},{"topic_id":"second","title":"Second"}]}`)
	_, err = api.database.CommitCache(context.Background(), session, store.CacheEntry{Resource: "research-training", Variant: "default", SchemaVersion: 1, Payload: payload, Revision: "fixture"}, "test", nil)
	if err != nil {
		t.Fatal(err)
	}
	start := make(chan struct{})
	codes := make(chan int, 2)
	for _, topic := range []string{"first", "second"} {
		go func(topic string) {
			<-start
			codes <- apiRequest(handler, "POST", "/api/research-training/favorite", `{"batch_id":"batch","topic_id":"`+topic+`","favorite":true}`, cookie).Code
		}(topic)
	}
	close(start)
	first, second := <-codes, <-codes
	if first != 200 || second != 200 {
		t.Fatalf("concurrent responses: %d, %d", first, second)
	}
	readIDs := func() []any {
		return researchResponse(t, handler, cookie, "GET", "/api/research-training/cache", "")["favorite_topic_ids"].([]any)
	}
	ids := readIDs()
	if len(ids) != 2 || ids[0] == ids[1] {
		t.Fatal(ids)
	}
	researchResponse(t, handler, cookie, "POST", "/api/research-training/favorite", `{"batch_id":"batch","topic_id":"first","favorite":false}`)
	ids = readIDs()
	if len(ids) != 1 || ids[0] != "second" {
		t.Fatal(ids)
	}
}

func TestResearchFavoriteSurvivesReopen(t *testing.T) {
	directory := t.TempDir()
	for _, phase := range []string{"save", "read-and-remove", "verify-removed"} {
		if !t.Run(phase, func(t *testing.T) {
			api, handler := testApplicationAt(t, "local-single", directory)
			cookie := loginCookie(t, handler, "student")
			if phase == "save" {
				session, err := api.database.Session(context.Background(), cookie.Value)
				if err != nil {
					t.Fatal(err)
				}
				commitResearchFixture(t, api, session, "batch", "原课题")
				researchResponse(t, handler, cookie, "POST", "/api/research-training/favorite", `{"batch_id":"batch","topic_id":"topic","favorite":true}`)
				return
			}
			result := researchResponse(t, handler, cookie, "GET", "/api/research-training/cache", "")
			favorites := result["favorite_topics"].([]any)
			if phase == "read-and-remove" {
				if len(favorites) != 1 || favorites[0].(map[string]any)["title"] != "原课题" {
					t.Fatal(result)
				}
				researchResponse(t, handler, cookie, "POST", "/api/research-training/favorite", `{"batch_id":"batch","topic_id":"topic","favorite":false}`)
			} else if len(favorites) != 0 {
				t.Fatal(result)
			}
		}) {
			return
		}
	}
}

func TestResearchFavoritesArchiveIsolationAndDeletion(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	commitResearchFixture(t, api, session, "batch", "原课题")
	body := `{"batch_id":"batch","topic_id":"topic","favorite":true}`
	result := researchResponse(t, handler, cookie, "POST", "/api/research-training/favorite", body)
	if len(result["favorite_topic_ids"].([]any)) != 1 {
		t.Fatal(result)
	}
	updated := commitResearchFixture(t, api, session, "batch", "新课题名")
	api.syncResearchFavorites(session, updated)
	commitResearchFixture(t, api, session, "next", "")
	result = researchResponse(t, handler, cookie, "GET", "/api/research-training/cache", "")
	favorite := result["favorite_topics"].([]any)[0].(map[string]any)
	if favorite["title"] != "新课题名" || favorite["expired"] != true || favorite["can_enroll"] != false || len(result["favorite_topic_ids"].([]any)) != 0 {
		t.Fatal(result)
	}
	if response := apiRequest(handler, "POST", "/api/research-training/favorite", body, cookie); response.Code != 409 {
		t.Fatal("expired topic can be favorited", response.Code)
	}
	researchResponse(t, handler, cookie, "POST", "/api/research-training/favorite", `{"batch_id":"batch","topic_id":"topic","favorite":false}`)
	api.syncResearchFavorites(session, updated)
	result = researchResponse(t, handler, cookie, "GET", "/api/research-training/cache", "")
	if len(result["favorite_topics"].([]any)) != 0 {
		t.Fatal("refresh revived removed favorite")
	}
	other := loginCookie(t, handler, "other-student")
	result = researchResponse(t, handler, other, "GET", "/api/research-training/cache", "")
	if result["available"] != false || len(result["favorite_topics"].([]any)) != 0 {
		t.Fatal("cross-account data exposed", result)
	}
}

func TestResearchFavoriteValidationAndGenericWriteProtection(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	commitResearchFixture(t, api, session, "batch", "课题")
	for _, body := range []string{`{}`, `{"batch_id":"batch","topic_id":"topic"}`, `{"batch_id":"batch","topic_id":"topic","favorite":true,"extra":1}`, `{"batch_id":"batch","topic_id":"\u0000","favorite":true}`} {
		if response := apiRequest(handler, "POST", "/api/research-training/favorite", body, cookie); response.Code != 422 {
			t.Fatal(response.Code, body)
		}
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if response := apiRequest(handler, method, "/api/documents/research-favorites/fake", `{"revision":0,"data":{}}`, cookie); response.Code != 403 {
			t.Fatal("generic favorite write allowed", response.Code)
		}
	}
}

func TestResearchSemanticAndChangeSummary(t *testing.T) {
	first := map[string]any{"batch": map[string]any{"batch_id": "batch"}, "topics": []map[string]any{{"topic_id": "b", "title": "旧"}, {"topic_id": "a"}}, "confirmed_topics": []any{}}
	second := map[string]any{"batch": map[string]any{"batch_id": "batch"}, "topics": []map[string]any{{"topic_id": "a"}, {"topic_id": "b", "title": "旧"}}, "confirmed_topics": []any{}}
	if string(researchSemantic(first)) != string(researchSemantic(second)) {
		t.Fatal("server sort order changed revision")
	}
	second["topics"] = []map[string]any{{"topic_id": "b", "title": "新"}, {"topic_id": "c"}}
	diff := researchDiff(researchSemantic(first), researchSemantic(second))
	if len(diff["added_topic_ids"].([]string)) != 1 || len(diff["changed_topic_ids"].([]string)) != 1 || len(diff["removed_topic_ids"].([]string)) != 1 {
		t.Fatal(diff)
	}
	second["batch"] = map[string]any{"batch_id": "next"}
	diff = researchDiff(researchSemantic(first), researchSemantic(second))
	if diff["batch_changed"] != true || len(diff["added_topic_ids"].([]string)) != 0 {
		t.Fatal("new batch treated as individual additions", diff)
	}
}

func TestResearchRefreshFailurePreservesCacheAndOfflineNeverFetches(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	commitResearchFixture(t, api, session, "batch", "保留课题")
	researchResponse(t, handler, cookie, "POST", "/api/research-training/favorite", `{"batch_id":"batch","topic_id":"topic","favorite":true}`)
	var calls atomic.Int32
	spec := api.resources.specs["research-training"]
	spec.Fetch = func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		calls.Add(1)
		return nil, nil, errors.New("synthetic failed page")
	}
	api.resources.Register("research-training", spec)
	if response := apiRequest(handler, "POST", "/api/research-training/refresh", "", cookie); response.Code == 200 {
		t.Fatal("failed refresh reported success")
	}
	result := researchResponse(t, handler, cookie, "GET", "/api/research-training/cache", "")
	if result["total"] != float64(1) || len(result["favorite_topics"].([]any)) != 1 || calls.Load() != 1 {
		t.Fatal(result, calls.Load())
	}
	if response := apiRequest(handler, "POST", "/api/logout", "", cookie); response.Code != 200 {
		t.Fatal(response.Code)
	}
	result = researchResponse(t, handler, nil, "GET", "/api/offline/research-training?refresh=true", "")
	if result["source"] != "offline" || len(result["favorite_topics"].([]any)) != 1 || calls.Load() != 1 {
		t.Fatal("offline refresh or lost favorite", result, calls.Load())
	}
}

func TestResearchListUsesFreshSharedSnapshotAndValidatesPaging(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	commitResearchFixture(t, api, session, "batch", "材料研究")
	result := researchResponse(t, handler, cookie, "GET", "/api/research-training?keyword=材料&page_size=1", "")
	if result["total"] != float64(1) || len(result["topics"].([]any)) != 1 {
		t.Fatal(result)
	}
	result = researchResponse(t, handler, cookie, "GET", "/api/research-training?keyword=不存在", "")
	if result["total"] != float64(0) {
		t.Fatal(result)
	}
	for _, query := range []string{"page=0", "page_size=51", "page=bad"} {
		if response := apiRequest(handler, "GET", "/api/research-training?"+query, "", cookie); response.Code != 422 {
			t.Fatal(response.Code, query)
		}
	}
	api.resources.mutex.Lock()
	defer api.resources.mutex.Unlock()
	if len(api.resources.jobs) != 0 {
		t.Fatal("fresh list started remote work")
	}
}
