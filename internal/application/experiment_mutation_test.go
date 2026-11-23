package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const experimentMutationBody = `{"term":"2026-2027-1","task_id":"task","project_code":"project","round_id":"round"}`

type experimentMutationClient struct {
	testCampusClient
	selected, outcome, current string
	writes                     int
	apply, full                bool
	beforeWrite                func()
	started, release           chan struct{}
}

func (client *experimentMutationClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	switch {
	case strings.HasSuffix(target, "queryCanSelectedCourses.do"):
		return json.Marshal(map[string]any{"code": 0, "datas": map[string]any{"queryCanSelectedCourses": []any{map[string]any{"taskId": "task", "courseNo": "course", "allExperimentProjectList": []any{map[string]any{"projectCode": "project", "selectedRoundId": client.selected}}}}}})
	case strings.HasSuffix(target, "queryStudentAllSelectCourseResult.do"):
		rows := []any{}
		if client.selected != "" {
			rows = append(rows, map[string]any{"taskId": "task", "projectResultList": []any{map[string]any{"projectCode": "project", "roundId": client.selected}}})
		}
		return json.Marshal(map[string]any{"code": 0, "datas": map[string]any{"queryStudentAllSelectCourseResult": rows}})
	case strings.HasSuffix(target, "queryTaskProjectRounds.do"):
		count := 0
		if client.full {
			count = 20
		}
		return json.Marshal(map[string]any{"code": 0, "datas": map[string]any{"queryTaskProjectRounds": []any{map[string]any{"wid": "round", "courseCapacity": 20, "selectedNums": count}}}})
	case strings.HasSuffix(target, "selectedMustDoProjectRound.do"):
		if method != "POST" || form.Get("SYRW_WID") != "task" || form.Get("SYXMDM") != "project" || form.Get("PKLC_WID") != "round" || form.Get("XNXQDM") != "2026-2027-1" {
			return nil, errors.New("invalid write contract")
		}
		if client.beforeWrite != nil {
			client.beforeWrite()
		}
		client.writes++
		if client.started != nil {
			close(client.started)
			<-client.release
		}
		if client.apply {
			client.selected = "round"
			if strings.HasSuffix(target, "deselectedMustDoProjectRound.do") {
				client.selected = ""
			}
		}
		switch client.outcome {
		case "lost":
			return nil, errors.New("response lost")
		case "rejected":
			return []byte(`{"code":1}`), nil
		case "malformed":
			return []byte(`{}`), nil
		}
		return []byte(`{"code":0}`), nil
	case strings.HasSuffix(target, "cxmrxnxq.do"):
		return json.Marshal(map[string]any{"code": 0, "datas": map[string]any{"cxmrxnxq": client.current}})
	case strings.HasSuffix(target, "xnxqcx.do"):
		return json.Marshal(map[string]any{"code": 0, "datas": map[string]any{"xnxqcx": []any{map[string]any{"DM": client.current}}}})
	}
	return nil, errors.New("unexpected request")
}

func experimentMutationFixture(t *testing.T) (*API, http.Handler, *http.Cookie, store.Session, *experimentMutationClient) {
	t.Helper()
	api, handler := testApplication(t, "local-single")
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mailTick = nil
	api.accounts.mutex.Unlock()
	client := &experimentMutationClient{testCampusClient: testCampusClient{account: "student"}, apply: true, current: "2027-2028-1"}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	for _, entry := range []store.CacheEntry{
		{Resource: "experiment-courses", Variant: "default", Payload: json.RawMessage(`{"term":"2026-2027-1","courses":[],"selected_results":[]}`)},
		{Resource: "academic-report", Variant: "default", Payload: json.RawMessage(`{}`)},
		{Resource: "personal-timetable", Variant: "term:2026-2027-1", Payload: json.RawMessage(`{"term_code":"2026-2027-1","courses":[]}`)},
		{Resource: "personal-timetable", Variant: "term:2027-2028-1", Payload: json.RawMessage(`{"term_code":"2027-2028-1","courses":[]}`)},
	} {
		entry.SchemaVersion = 1
		entry.Revision = "baseline"
		if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	return api, handler, cookie, session, client
}

func experimentWrite(t *testing.T, handler http.Handler, cookie *http.Cookie, action string) map[string]any {
	t.Helper()
	response := apiRequest(handler, "POST", "/api/experiment-courses/"+action, experimentMutationBody, cookie)
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	var result map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestExperimentMutationDeduplicatesCyclesAndInvalidatesOnlyTargetTerm(t *testing.T) {
	api, handler, cookie, session, client := experimentMutationFixture(t)
	client.beforeWrite = func() {
		document, err := api.database.Read(context.Background(), session.Account, "experiment-operations", experimentOperationKey(academic.ExperimentMutation{Term: "2026-2027-1", Task: "task", Project: "project", Round: "round"}))
		if err != nil || !strings.Contains(string(document.Payload), `"state":"pending"`) {
			t.Fatal("write was sent before intent persisted", err)
		}
	}
	for _, action := range []string{"select", "select", "deselect", "deselect", "select"} {
		if result := experimentWrite(t, handler, cookie, action); result["code"] != "0" {
			t.Fatal(result)
		}
	}
	if client.writes != 3 {
		t.Fatal("duplicate or missing writes", client.writes)
	}
	for _, item := range []struct {
		resource, variant string
		stale             bool
	}{{"experiment-courses", "default", true}, {"academic-report", "default", true}, {"personal-timetable", "term:2026-2027-1", true}, {"personal-timetable", "term:2027-2028-1", false}} {
		entry, err := api.database.ReadCache(context.Background(), session, item.resource, item.variant)
		if err != nil || entry.Invalidated != item.stale {
			t.Fatal("incorrect cache invalidation", item, err)
		}
	}
	api.resources.mutex.Lock()
	defer api.resources.mutex.Unlock()
	for _, job := range api.resources.jobs {
		if job.Resource == "personal-timetable" {
			t.Fatal("historical mutation scheduled timetable refresh")
		}
	}
}

func TestExperimentUnknownNeverReplaysUntilOfficialResultConfirms(t *testing.T) {
	api, handler, cookie, session, client := experimentMutationFixture(t)
	client.outcome, client.apply = "lost", false
	for _, action := range []string{"select", "select", "deselect"} {
		if result := experimentWrite(t, handler, cookie, action); result["state"] != "unknown" {
			t.Fatal(result)
		}
	}
	if client.writes != 1 {
		t.Fatal("unknown operation replayed")
	}
	entry, _ := api.database.ReadCache(context.Background(), session, "academic-report", "default")
	if entry.Invalidated {
		t.Fatal("unconfirmed result mutated cache")
	}
	client.selected = "round"
	if result := experimentWrite(t, handler, cookie, "select"); result["code"] != "0" || client.writes != 1 {
		t.Fatal("official confirmation did not reconcile", result)
	}
}

func TestExperimentRejectedAndFullDoNotChangeCache(t *testing.T) {
	for _, full := range []bool{false, true} {
		t.Run(map[bool]string{false: "rejected", true: "full"}[full], func(t *testing.T) {
			api, handler, cookie, session, client := experimentMutationFixture(t)
			client.full, client.outcome, client.apply = full, "rejected", false
			if result := experimentWrite(t, handler, cookie, "select"); result["code"] == "0" {
				t.Fatal(result)
			}
			if full && client.writes != 0 || !full && client.writes != 1 {
				t.Fatal("preflight/send count", client.writes)
			}
			entry, _ := api.database.ReadCache(context.Background(), session, "experiment-courses", "default")
			if entry.Invalidated {
				t.Fatal("failure changed cache")
			}
			client.full, client.outcome, client.apply = false, "", true
			if result := experimentWrite(t, handler, cookie, "select"); result["code"] != "0" {
				t.Fatal("explicit retry after rejection failed", result)
			}
		})
	}
}

func TestExperimentLostResponseCanBeConfirmedWithoutReplay(t *testing.T) {
	_, handler, cookie, _, client := experimentMutationFixture(t)
	client.outcome = "lost"
	if result := experimentWrite(t, handler, cookie, "select"); result["code"] != "0" || client.writes != 1 {
		t.Fatal(result)
	}
}

func TestExperimentVerifyNeverSendsMutation(t *testing.T) {
	_, handler, cookie, _, client := experimentMutationFixture(t)
	if result := experimentWrite(t, handler, cookie, "verify"); result["state"] != "not_sent" || client.writes != 0 {
		t.Fatal(result, client.writes)
	}
	client.outcome, client.apply = "lost", false
	experimentWrite(t, handler, cookie, "select")
	if result := experimentWrite(t, handler, cookie, "verify"); result["state"] != "unknown" || client.writes != 1 {
		t.Fatal(result, client.writes)
	}
	client.selected = "round"
	if result := experimentWrite(t, handler, cookie, "verify"); result["state"] != "confirmed" || client.writes != 1 {
		t.Fatal(result, client.writes)
	}
}

func TestExperimentConfirmedOperationAllowsExternalStateChange(t *testing.T) {
	_, handler, cookie, _, client := experimentMutationFixture(t)
	if result := experimentWrite(t, handler, cookie, "select"); result["state"] != "confirmed" {
		t.Fatal(result)
	}
	client.selected = ""
	if result := experimentWrite(t, handler, cookie, "select"); result["state"] != "confirmed" || client.writes != 2 {
		t.Fatal("external withdrawal blocked new selection", result, client.writes)
	}
}

func TestExperimentRevocationPreservesPendingEvidence(t *testing.T) {
	api, handler, cookie, session, client := experimentMutationFixture(t)
	client.beforeWrite = func() {
		if err := api.database.RevokeAccount(context.Background(), session.Account); err != nil {
			t.Fatal(err)
		}
	}
	if result := experimentWrite(t, handler, cookie, "select"); result["state"] != "unknown" || client.writes != 1 {
		t.Fatal(result, client.writes)
	}
	document, err := api.database.Read(context.Background(), session.Account, "experiment-operations", experimentOperationKey(academic.ExperimentMutation{Term: "2026-2027-1", Task: "task", Project: "project"}))
	if err != nil || !strings.Contains(string(document.Payload), `"state":"pending"`) {
		t.Fatal("revoked result overwrote pending evidence", document, err)
	}
}

func TestExperimentMutationInputAndLedgerProtection(t *testing.T) {
	_, handler, cookie, _, client := experimentMutationFixture(t)
	for _, body := range []string{`{}`, `{"term":"x","task_id":"task","project_code":"project","round_id":"round","extra":true}`, strings.Replace(experimentMutationBody, `"round"`, `"\u0000"`, 1)} {
		if response := apiRequest(handler, "POST", "/api/experiment-courses/select", body, cookie); response.Code != 422 {
			t.Fatal(response)
		}
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if response := apiRequest(handler, method, "/api/documents/experiment-operations/record", `{"revision":0,"data":{}}`, cookie); response.Code != 403 {
			t.Fatal("ledger editable through generic API")
		}
	}
	if client.writes != 0 {
		t.Fatal("invalid input wrote")
	}
}

func TestExperimentCurrentTermRebuildsThroughSharedJob(t *testing.T) {
	api, handler, cookie, session, client := experimentMutationFixture(t)
	client.current = "2026-2027-1"
	var reads atomic.Int32
	api.resources.Register("personal-timetable", ResourceSpec{Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		reads.Add(1)
		return json.RawMessage(`{"term_code":"2026-2027-1","courses":[]}`), nil, nil
	}})
	if result := experimentWrite(t, handler, cookie, "select"); result["code"] != "0" {
		t.Fatal(result)
	}
	api.resources.mutex.Lock()
	identifier := ""
	for id, job := range api.resources.jobs {
		if job.Resource == "personal-timetable" {
			identifier = id
		}
	}
	api.resources.mutex.Unlock()
	if identifier == "" {
		t.Fatal("current term rebuild missing")
	}
	if job := waitTestJob(t, api.resources, session, map[string]any{"job_id": identifier}); job.Status != "completed" || reads.Load() != 1 {
		t.Fatal(job)
	}
}
