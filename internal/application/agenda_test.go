package application

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestAgendaRoundTripConflictAndDedicatedValidation(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	path := "/api/timetable/agenda?term_code=2026-2027-1"
	response := apiRequest(handler, "GET", path, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"revision":0`) {
		t.Fatal(response.Body.String())
	}
	body := `{"revision":0,"events":[{"id":"event","title":"个人安排","date":"2099-01-02","start_time":"09:00","end_time":"10:00"}],"moves":[]}`
	response = apiRequest(handler, "PUT", path, body, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"revision":1`) || !strings.Contains(response.Body.String(), `"dates":["2099-01-02"]`) {
		t.Fatal(response.Body.String())
	}
	if response = apiRequest(handler, "PUT", path, body, cookie); response.Code != 409 {
		t.Fatal("stale tab overwrote agenda")
	}
	response = apiRequest(handler, "PUT", "/api/documents/agenda/2026-2027-1", `{"revision":1,"data":{}}`, cookie)
	if response.Code != 403 {
		t.Fatal("generic route bypassed agenda validation")
	}
	session, _ := api.database.Session(context.Background(), cookie.Value)
	if _, err := api.database.ReadCache(context.Background(), session, "personal-timetable", "term:2026-2027-1"); err != store.ErrMissing {
		t.Fatal("agenda modified official cache")
	}
	response = apiRequest(handler, "PUT", path, `{"revision":1,"events":[],"moves":[{"source":"2000-01-01","target":"2099-01-01"}]}`, cookie)
	if response.Code != 422 {
		t.Fatal("past source accepted")
	}
	response = apiRequest(handler, "GET", path, "", cookie)
	if !strings.Contains(response.Body.String(), "个人安排") {
		t.Fatal("failed write lost agenda")
	}
}

func TestAgendaSurvivesReopenAndRejectsStaleWriter(t *testing.T) {
	directory := t.TempDir()
	const path = "/api/timetable/agenda?term_code=2026-2027-1"
	const draft = `{"revision":0,"events":[{"id":"event","title":"个人安排","date":"2099-01-02","start_time":"09:00","end_time":"10:00"}],"moves":[{"source":"2099-01-04","target":"2099-01-05"}]}`
	var saved map[string]any
	if !t.Run("save", func(t *testing.T) {
		_, handler := testApplicationAt(t, "local-single", directory)
		cookie := loginCookie(t, handler, "20250001")
		response := apiRequest(handler, "PUT", path, draft, cookie)
		if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &saved) != nil || saved["revision"] != float64(1) || len(saved["events"].([]any)) != 1 || len(saved["moves"].([]any)) != 1 {
			t.Fatal(response.Body.String())
		}
	}) {
		return
	}
	_, handler := testApplicationAt(t, "local-single", directory)
	cookie := loginCookie(t, handler, "20250001")
	stale := apiRequest(handler, "PUT", path, `{"revision":0,"events":[],"moves":[]}`, cookie)
	if stale.Code != 409 {
		t.Fatal(stale.Code, stale.Body.String())
	}
	response := apiRequest(handler, "GET", path, "", cookie)
	var loaded map[string]any
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &loaded) != nil || !reflect.DeepEqual(saved, loaded) {
		t.Fatal(saved, response.Body.String())
	}
	deleted := apiRequest(handler, "PUT", path, `{"revision":1,"events":[],"moves":[]}`, cookie)
	if deleted.Code != 200 {
		t.Fatal(deleted.Body.String())
	}
	oldDocument, err := json.Marshal(map[string]any{"revision": saved["revision"], "events": saved["events"], "moves": saved["moves"]})
	if err != nil {
		t.Fatal(err)
	}
	stale = apiRequest(handler, "PUT", path, string(oldDocument), cookie)
	if stale.Code != 409 {
		t.Fatalf("saved copy after deletion returned %d: %s", stale.Code, stale.Body.String())
	}
	response = apiRequest(handler, "GET", path, "", cookie)
	var current struct {
		Revision int               `json:"revision"`
		Events   []json.RawMessage `json:"events"`
		Moves    []json.RawMessage `json:"moves"`
	}
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &current) != nil || current.Revision != 2 || len(current.Events) != 0 || len(current.Moves) != 0 {
		t.Fatal(response.Body.String())
	}
}

func TestAgendaCalendarAndRetirementRequireStartedNextSemester(t *testing.T) {
	if semesterCalendar([]map[string]any{{"number": 1, "start_date": "2026-03-01", "end_date": "2026-03-07"}, {"number": 3, "start_date": "2026-03-15", "end_date": "2026-03-21"}}) != nil {
		t.Fatal("incomplete calendar accepted")
	}
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	initial := agendaRecord{Events: []AgendaEvent{{ID: "old", Title: "保留"}}, Moves: []AgendaMove{}, Semester: &semesterSpan{Start: "2026-03-01", End: "2026-07-04"}}
	encoded, _ := json.Marshal(initial)
	if _, err := api.database.SaveForSession(context.Background(), session, "agenda", "old", encoded, 0, false); err != nil {
		t.Fatal(err)
	}
	calendars := map[string]*semesterSpan{"next": {Start: "2026-09-06", End: "2027-01-02"}}
	if err := api.reconcileAgendas(context.Background(), session, calendars, "next", "2026-08-01"); err != nil {
		t.Fatal(err)
	}
	document, _ := api.database.Read(context.Background(), session.Account, "agenda", "old")
	if document.Revision != 1 || !strings.Contains(string(document.Payload), "保留") {
		t.Fatal("future preview retired old data")
	}
	if err := api.reconcileAgendas(context.Background(), session, calendars, "next", "2026-09-07"); err != nil {
		t.Fatal(err)
	}
	document, _ = api.database.Read(context.Background(), session.Account, "agenda", "old")
	var record agendaRecord
	json.Unmarshal(document.Payload, &record)
	if document.Revision != 2 || !record.Retired || len(record.Events) != 0 {
		t.Fatalf("retirement: %+v %+v", document, record)
	}
	if _, err := api.database.SaveForSession(context.Background(), session, "agenda", "old", encoded, 1, false); err != store.ErrConflict {
		t.Fatal("old draft revived retired data")
	}
}

func TestAgendaWeekNormalizationAndValidation(t *testing.T) {
	weekday := 2
	document := AgendaDocument{Events: []AgendaEvent{{ID: "one", Mode: "week", Weekday: &weekday, Weeks: []int{4, 2, 2}, Title: "按周", Start: "08:00", End: "09:00"}}}
	if !document.normalize() || len(document.Events[0].Weeks) != 2 || document.Events[0].Weeks[0] != 2 || document.Events[0].Date != nil {
		t.Fatal(document)
	}
	document.Events = append(document.Events, document.Events[0])
	if document.normalize() {
		t.Fatal("duplicate event accepted")
	}
}
