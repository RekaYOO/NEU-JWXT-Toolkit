package application

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type jwxkScanClient struct {
	jwxkCatalogClient
	pages        []int
	failSecond   bool
	shortFirst   bool
	repeatSecond bool
	onPage       func(int)
}

func (client *jwxkScanClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if input.Path != "/xsxk/elective/clazz/list" {
		return client.jwxkCatalogClient.RequestService(ctx, input)
	}
	var query map[string]any
	json.Unmarshal(input.JSON, &query)
	page := int(academic.Number(query["pageNumber"]))
	if query["teachingClassType"] != "FANKC" || academic.Number(query["pageSize"]) != 50 {
		return nil, errors.New("scan used wrong scope or page size")
	}
	client.pages = append(client.pages, page)
	if client.onPage != nil {
		client.onPage(page)
	}
	if client.failSecond && page == 2 {
		return nil, &campus.Error{Code: "SERVICE_RATE_LIMITED", Message: "稍后再试"}
	}
	count := 50
	if page == 2 {
		count = 1
	}
	if client.shortFirst {
		count = 2
	}
	rows := []map[string]any{}
	for index := 0; index < count; index++ {
		id := fmt.Sprintf("class-%d", (page-1)*50+index)
		if client.repeatSecond && page == 2 {
			id = "class-0"
		}
		rows = append(rows, map[string]any{"KCH": id, "JXBID": id, "KCM": id, "KRL": 20, "YXRS": 2})
	}
	return json.Marshal(map[string]any{"code": 200, "data": map[string]any{"total": 51, "rows": rows}})
}

func TestJWXKScanFullPagesAndFailurePreserveProgress(t *testing.T) {
	for _, scenario := range []string{"complete", "failed", "short", "deleted", "repeated"} {
		t.Run(scenario, func(t *testing.T) {
			api, _ := testApplication(t, "remote-multi")
			ctx := context.Background()
			api.database.AddAccount(ctx, "student-a")
			generation, _ := api.database.Generation(ctx, "student-a")
			session := store.Session{Account: "student-a", Generation: generation}
			client := &jwxkScanClient{failSecond: scenario == "failed", shortFirst: scenario == "short", repeatSecond: scenario == "repeated"}
			runtime := &Runtime{Client: client, Context: ctx, Generation: generation}
			if err := api.mergeJWXKArchive(ctx, session, "round", store.Document{}, nil, map[string]any{"groups": []any{}}); err != nil {
				t.Fatal(err)
			}
			if scenario == "deleted" {
				client.onPage = func(page int) {
					if page != 2 {
						return
					}
					document, _, _ := api.readJWXKArchive(ctx, session, "round")
					if _, err := api.database.SaveForSession(ctx, session, jwxkArchiveNamespace, document.Key, []byte("null"), document.Revision, true); err != nil {
						t.Fatal(err)
					}
				}
			}
			err := api.scanJWXKCatalog(ctx, session, runtime, "round", "direct")
			_, archive, readError := api.readJWXKArchive(ctx, session, "round")
			if readError != nil {
				t.Fatal(readError)
			}
			if scenario == "deleted" {
				if err == nil || archive != nil {
					t.Fatal("late page resurrected deletion", err, archive)
				}
				return
			}
			if scenario == "complete" {
				if err != nil || archive["catalog_complete"] != true || archive["sync_status"] != "complete" || len(jwxkArchiveRows(archive["courses"])) != 51 || academic.Number(archive["sync_loaded"]) != 51 {
					t.Fatal(err, archive)
				}
			} else {
				if err == nil || archive["catalog_complete"] == true || archive["sync_status"] != "failed" {
					t.Fatal("incomplete scan marked successful", err, archive)
				}
				if scenario == "failed" && len(jwxkArchiveRows(archive["courses"])) != 50 {
					t.Fatal("successful first page lost")
				}
			}
		})
	}
}

func TestDocumentBackgroundWorkDeduplicatesAndCancels(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	started := make(chan struct{})
	work := func(ctx context.Context, runtime *Runtime) error {
		close(started)
		<-ctx.Done()
		return ctx.Err()
	}
	first, err := api.resources.startDocumentWork(session, "test-document", "round", work)
	if err != nil {
		t.Fatal(err)
	}
	<-started
	second, err := api.resources.startDocumentWork(session, "test-document", "round", work)
	if err != nil || first != second {
		t.Fatal("duplicate job", first, second, err)
	}
	api.accounts.Runtime(session).Cancel()
	api.resources.mutex.Lock()
	job := api.resources.jobs[first]
	api.resources.mutex.Unlock()
	select {
	case <-job.done:
	case <-time.After(time.Second):
		t.Fatal("job ignored session cancellation")
	}
	result, ok := api.resources.Job(session, first)
	if !ok || result.Status != "cancelled" {
		t.Fatal(result)
	}
	if _, ok := api.resources.Job(store.Session{Account: "student-b", Generation: generation}, first); ok {
		t.Fatal("other account read job")
	}
}

func TestJWXKScanResumesAfterLoginAndServesCompleteArchive(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	if err := api.mergeJWXKArchive(ctx, session, "round", store.Document{}, nil, map[string]any{"groups": []any{}}); err != nil {
		t.Fatal(err)
	}
	client := &jwxkScanClient{}
	client.account = "student-a"
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	api.resources.mutex.Lock()
	var scan *CacheJob
	for _, job := range api.resources.jobs {
		if job.Resource == "jwxk-catalog" {
			scan = job
		}
	}
	api.resources.mutex.Unlock()
	if scan == nil {
		t.Fatal("login did not resume persisted archive")
	}
	select {
	case <-scan.done:
	case <-time.After(5 * time.Second):
		t.Fatal("resumed scan did not finish")
	}
	response := apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/search", `{"batch_code":"round","page_number":3,"local_only":true}`, cookie)
	var result map[string]any
	json.Unmarshal(response.Body.Bytes(), &result)
	if response.Code != 200 || result["catalog_complete"] != true || academic.Number(result["total"]) != 51 || len(jwxkArchiveRows(result["groups"])) != 11 {
		t.Fatal(response)
	}
	if len(client.pages) != 2 {
		t.Fatal("local query contacted school", client.pages)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/filter-options", `{"batch_code":"round"}`, cookie)
	if response.Code != 200 || len(client.pages) != 2 {
		t.Fatal("filters ignored archive", response)
	}
}
