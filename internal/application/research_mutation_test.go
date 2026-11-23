package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const researchEnrollmentBody = `{"topic_id":"topic","batch_id":"batch","phone":"123456","email":"test@example.invalid","reason":"申请"}`

type researchMutationClient struct {
	testCampusClient
	selected, apply, unavailable, full, hidden bool
	outcome                                    string
	writes                                     int
	beforeWrite                                func()
}

func (client *researchMutationClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	switch {
	case strings.HasSuffix(target, "cxdqxnxqpc.do"):
		return []byte(`{"code":"0","datas":{"cxdqxnxqpc":{"WID":"batch"}}}`), nil
	case strings.HasSuffix(target, "ktbmcxlb.do"):
		row := map[string]any{"WID": "topic", "PCWID": "batch", "ZSXSS": 10, "YBMRS": 0, "BMZT": "10"}
		if client.selected {
			row["BMWID"] = "registration"
		}
		if client.full {
			row["YBMRS"] = 10
		}
		rows := []any{row}
		if client.hidden {
			rows = []any{}
		}
		return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"ktbmcxlb": map[string]any{"rows": rows, "totalSize": len(rows)}}})
	case strings.HasSuffix(target, "cxxsjdjzypm.do"):
		if client.unavailable {
			return []byte(`{"code":"0","datas":{"cxxsjdjzypm":[]}}`), nil
		}
		return []byte(`{"code":"0","datas":{"cxxsjdjzypm":[{"PJJD":"3.6","ZYPM":".25"}]}}`), nil
	case strings.HasSuffix(target, "/save.do"), strings.HasSuffix(target, "/qxbm.do"):
		if method != "POST" {
			return nil, errors.New("invalid mutation method")
		}
		if client.beforeWrite != nil {
			client.beforeWrite()
		}
		client.writes++
		if client.apply {
			client.selected = strings.HasSuffix(target, "/save.do")
		}
		if client.outcome == "lost" {
			return nil, errors.New("synthetic disconnect")
		}
		if client.outcome == "rejected" {
			return []byte(`{"code":"1"}`), nil
		}
		return []byte(`{"code":"0"}`), nil
	}
	return nil, errors.New("unexpected request")
}

func researchMutationFixture(t *testing.T) (*API, http.Handler, *http.Cookie, store.Session, *researchMutationClient) {
	t.Helper()
	api, handler := testApplication(t, "local-single")
	client := &researchMutationClient{testCampusClient: testCampusClient{account: "student"}, apply: true}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	api.resources.Register("research-training", ResourceSpec{Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		return nil, nil, errors.New("synthetic refresh disabled")
	}})
	return api, handler, cookie, session, client
}

func researchWrite(t *testing.T, handler http.Handler, cookie *http.Cookie, action string) map[string]any {
	t.Helper()
	body := `{"topic_id":"topic"}`
	if action == "enroll" {
		body = researchEnrollmentBody
	}
	return researchResponse(t, handler, cookie, "POST", "/api/research-training/"+action, body)
}

func TestResearchMutationPersistsBeforeSendingAndDeduplicatesCycles(t *testing.T) {
	api, handler, cookie, session, client := researchMutationFixture(t)
	client.beforeWrite = func() {
		document, err := api.database.Read(context.Background(), session.Account, "research-operations", researchFavoriteKey("operation", "topic"))
		if err != nil || !strings.Contains(string(document.Payload), `"state":"pending"`) || strings.Contains(string(document.Payload), "example") || strings.Contains(string(document.Payload), "申请") {
			t.Fatal("missing intent or persisted form", document, err)
		}
	}
	for _, action := range []string{"enroll", "enroll", "cancel", "cancel", "enroll"} {
		if result := researchWrite(t, handler, cookie, action); result["state"] != "confirmed" {
			t.Fatal(action, result)
		}
	}
	if client.writes != 3 {
		t.Fatal("replayed operation", client.writes)
	}
}

func TestResearchUnknownOperationOnlyVerifiesAndMissingTopicDoesNotConfirmCancellation(t *testing.T) {
	_, handler, cookie, _, client := researchMutationFixture(t)
	client.selected, client.outcome, client.apply = true, "lost", false
	for _, action := range []string{"cancel", "cancel", "enroll", "verify"} {
		if result := researchWrite(t, handler, cookie, action); result["state"] != "unknown" {
			t.Fatal(action, result)
		}
	}
	client.hidden = true
	if result := researchWrite(t, handler, cookie, "verify"); result["state"] != "unknown" {
		t.Fatal("disappeared topic falsely confirmed cancellation", result)
	}
	client.hidden, client.selected = false, false
	if result := researchWrite(t, handler, cookie, "verify"); result["state"] != "confirmed" || client.writes != 1 {
		t.Fatal(result, client.writes)
	}
}

func TestResearchLostResponseReadbackAndPreflight(t *testing.T) {
	for _, scenario := range []string{"lost-applied", "full", "unavailable", "rejected", "verify-empty"} {
		t.Run(scenario, func(t *testing.T) {
			_, handler, cookie, _, client := researchMutationFixture(t)
			action, expected, writes := "enroll", "not_sent", 0
			switch scenario {
			case "lost-applied":
				client.outcome = "lost"
				expected, writes = "confirmed", 1
			case "full":
				client.full = true
			case "unavailable":
				client.unavailable = true
			case "rejected":
				client.outcome, client.apply = "rejected", false
				expected, writes = "rejected", 1
			case "verify-empty":
				action = "verify"
			}
			if result := researchWrite(t, handler, cookie, action); result["state"] != expected || client.writes != writes {
				t.Fatal(result, client.writes)
			}
		})
	}
}

func TestResearchRevocationRetainsUnknownEvidence(t *testing.T) {
	api, handler, cookie, session, client := researchMutationFixture(t)
	client.beforeWrite = func() {
		if err := api.database.RevokeAccount(context.Background(), session.Account); err != nil {
			t.Fatal(err)
		}
	}
	if result := researchWrite(t, handler, cookie, "enroll"); result["state"] != "unknown" {
		t.Fatal(result)
	}
	document, err := api.database.Read(context.Background(), session.Account, "research-operations", researchFavoriteKey("operation", "topic"))
	if err != nil || !strings.Contains(string(document.Payload), `"state":"pending"`) {
		t.Fatal(document, err)
	}
}

func TestResearchConcurrentSubmissionSendsOnce(t *testing.T) {
	_, handler, cookie, _, client := researchMutationFixture(t)
	started, release := make(chan struct{}), make(chan struct{})
	client.beforeWrite = func() { close(started); <-release }
	results := make(chan int, 2)
	go func() {
		results <- apiRequest(handler, "POST", "/api/research-training/enroll", researchEnrollmentBody, cookie).Code
	}()
	<-started
	go func() {
		results <- apiRequest(handler, "POST", "/api/research-training/enroll", researchEnrollmentBody, cookie).Code
	}()
	close(release)
	for index := 0; index < 2; index++ {
		if status := <-results; status != 200 {
			t.Fatal(status)
		}
	}
	if client.writes != 1 {
		t.Fatal("concurrent submit replayed", client.writes)
	}
}

func TestResearchMutationValidationAndLedgerProtection(t *testing.T) {
	_, handler, cookie, _, client := researchMutationFixture(t)
	for _, body := range []string{`{}`, strings.Replace(researchEnrollmentBody, `"123456"`, `""`, 1), strings.Replace(researchEnrollmentBody, `"申请"`, `"申请","gpa":4.0`, 1)} {
		if response := apiRequest(handler, "POST", "/api/research-training/enroll", body, cookie); response.Code != 422 {
			t.Fatal(response.Code, body)
		}
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if response := apiRequest(handler, method, "/api/documents/research-operations/record", `{"revision":0,"data":{}}`, cookie); response.Code != 403 {
			t.Fatal("ledger editable", response.Code)
		}
	}
	if client.writes != 0 {
		t.Fatal("invalid input sent")
	}
}

func TestResearchConfirmedOperationAllowsLaterOfficialChanges(t *testing.T) {
	_, handler, cookie, _, client := researchMutationFixture(t)
	if result := researchWrite(t, handler, cookie, "enroll"); result["state"] != "confirmed" {
		t.Fatal(result)
	}
	client.selected = false
	if result := researchWrite(t, handler, cookie, "enroll"); result["state"] != "confirmed" || client.writes != 2 {
		t.Fatal("external cancellation blocked new enrollment", result, client.writes)
	}
}
