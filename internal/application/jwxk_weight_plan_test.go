package application

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type jwxkWeightPlanClient struct {
	testCampusClient
	missing   bool
	calls     int
	mutations int
}

func (client *jwxkWeightPlanClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	client.calls++
	switch input.Path {
	case "/xsxk/web/now":
		return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
	case "/xsxk/web/studentInfo", "/xsxk/elective/user":
		return []byte(`{"code":200,"data":{"remainingWeight":20,"totalWeight":20,"student":{"electiveBatchList":[{"code":"round","name":"权重轮次","typeCode":"04","schoolTerm":"2026-2027-1","clazzTypeList":["FANKC"]}]}}}`), nil
	case "/xsxk/elective/clazz/list":
		bidders := "20"
		if client.missing {
			bidders = "null"
		}
		return []byte(fmt.Sprintf(`{"code":200,"data":{"total":1,"rows":[{"KCH":"CODE","KCM":"课程","JXBID":"class","KRL":10,"YXRS":0,"QZXKRS":%s}]}}`, bidders)), nil
	case "/xsxk/elective/select", "/xsxk/volunteer/select", "/xsxk/volunteer/xgxk/select", "/xsxk/elective/neu/deselect", "/xsxk/elective/batch":
		return []byte(`{"code":200,"data":[]}`), nil
	default:
		if strings.Contains(input.Path, "/add") || strings.Contains(input.Path, "/del") {
			client.mutations++
		}
		return []byte(`{"code":200,"data":[]}`), nil
	}
}

func TestJWXKWeightPlanHTTPRefreshesMarketAndNeverSubmits(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	client := &jwxkWeightPlanClient{testCampusClient: testCampusClient{account: "student-a"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	archive := map[string]any{"archive_id": jwxkArchiveID("round"), "batch_code": "round", "catalog_complete": true, "selection_type_code": "04", "term_code": "2026-2027-1", "courses": []any{}}
	encoded, _ := json.Marshal(archive)
	api.database.SaveForSession(ctx, session, jwxkArchiveNamespace, jwxkArchiveID("round"), encoded, 0, false)
	body := `{"batch_code":"round","term_code":"2026-2027-1","grade_size":7000,"groups":[{"group_id":"g","name":"目标","target_count":1}],"items":[{"class_id":"class","course_code":"CODE","plan_group_id":"g","utility":9}]}`
	endpoint := "/api/course-selection/jwxk/weights/plan"
	response := apiRequest(handler, "POST", endpoint, body, nil)
	if response.Code != 401 || client.calls != 0 {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"weight":20`) || !strings.Contains(response.Body.String(), `"classification":"COMP"`) || client.mutations != 0 {
		t.Fatal(response, client.calls, client.mutations)
	}
	before := client.calls
	response = apiRequest(handler, "GET", "/api/course-selection/jwxk/weights/config?term_code=2026-2027-1", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"grade_size":7000`) || client.calls != before {
		t.Fatal(response)
	}
	client.missing = true
	response = apiRequest(handler, "POST", endpoint, body, cookie)
	if response.Code != 422 || client.mutations != 0 {
		t.Fatal("missing live market accepted", response)
	}
}

func TestJWXKWeightConfigIsOfflineIsolatedAndMigratesLegacyDefault(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(ctx, account)
	}
	clients := map[string]*jwxkWeightPlanClient{}
	api.accounts.factory = func(account string) (CampusClient, error) {
		client := &jwxkWeightPlanClient{testCampusClient: testCampusClient{account: account}}
		clients[account] = client
		return client, nil
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	key := "weight-grade-2026-2027-1"
	api.database.SaveForSession(ctx, session, "settings", key, []byte(`{"grade_size":360}`), 0, false)
	clients["student-a"].Close()
	endpoint := "/api/course-selection/jwxk/weights/config?term_code=2026-2027-1"
	response := apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"grade_size":5000`) || clients["student-a"].calls != 0 {
		t.Fatal(response)
	}
	document, _ := api.database.Read(ctx, session.Account, "settings", key)
	if !strings.Contains(string(document.Payload), `"grade_size":5000`) {
		t.Fatal("legacy default not persisted", document)
	}
	api.database.SaveForSession(ctx, session, "settings", key, []byte(`{"grade_size":8000}`), document.Revision, false)
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"grade_size":5000`) {
		t.Fatal("other account observed private preference", response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"grade_size":8000`) {
		t.Fatal(response)
	}
}
