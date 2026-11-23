package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestGradeTrackingConfigPreservesPartialFieldsAndAccountIsolation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(context.Background(), account)
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	endpoint := "/api/grade-tracking/config"
	if response := apiRequest(handler, "GET", endpoint, "", nil); response.Code != 401 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "GET", endpoint, "", cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"interval_minutes":30`) || !strings.Contains(response.Body.String(), `"enabled":false`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "PUT", endpoint, `{"start_hour":7,"interval_minutes":15}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"end_hour":21`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"start_hour":9`) {
		t.Fatal("cross-account setting leak", response)
	}
	for _, body := range []string{`{"interval_minutes":4}`, `{"start_hour":22}`, `{"end_hour":25}`, `{"enabled":true}`, `{"interval_minutes":5.5}`} {
		if response := apiRequest(handler, "PUT", endpoint, body, cookieA); response.Code != 422 {
			t.Fatal(response)
		}
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if response := apiRequest(handler, method, "/api/documents/grade-tracking/config", `{"data":{"enabled":true},"revision":1}`, cookieA); response.Code != 403 {
			t.Fatal(response)
		}
	}
}

func TestTrackingActivationQueueDeliveryAndReenable(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.accounts.mutex.Lock()
	api.accounts.automationTick, api.accounts.mailTick = nil, nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	cookie := loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	endpoint := "/api/grade-tracking/enabled"
	if response := apiRequest(handler, "PATCH", endpoint, `{"enabled":true}`, cookie); response.Code != 400 {
		t.Fatal(response)
	}
	mail := `{"smtp_host":"smtp.example.invalid","from_email":"sender@example.invalid","to_email":"recipient@example.invalid"}`
	if response := apiRequest(handler, "PUT", "/api/system-settings/mail", mail, cookie); response.Code != 200 {
		t.Fatal(response)
	}
	api.resources.Register("scores", ResourceSpec{Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		payload := json.RawMessage(`{"scores":[{"name":"<课程>","code":"A","term":"2026-1","score":"90","gpa":4,"credit":2}],"overall_gpa":3.8}`)
		return payload, payload, nil
	}})
	for cycle := 0; cycle < 2; cycle++ {
		if response := apiRequest(handler, "PATCH", endpoint, `{"enabled":true}`, cookie); response.Code != 200 {
			t.Fatal(response)
		}
		state, document, _ := api.readTrackingState(ctx, session)
		activation := academic.Text(state["activation_id"])
		if response := apiRequest(handler, "PATCH", endpoint, `{"enabled":true}`, cookie); response.Code != 200 {
			t.Fatal(response)
		}
		_, same, _ := api.readTrackingState(ctx, session)
		if same.Revision != document.Revision {
			t.Fatal("idempotent enable created another activation")
		}
		for check := 0; check < 2; check++ {
			if response := apiRequest(handler, "POST", "/api/grade-tracking/check", "", cookie); response.Code != 200 {
				t.Fatal(response)
			}
		}
		count, _ := api.database.PendingMailCount(ctx, session, "grade_tracking")
		if count != 1 {
			t.Fatal("initial notification missing or duplicated", count)
		}
		state, _, _ = api.readTrackingState(ctx, session)
		next, err := time.Parse(time.RFC3339Nano, academic.Text(state["next_check_at"]))
		if err != nil || !next.After(time.Now().Add(29*time.Minute)) {
			t.Fatal("next check not scheduled", state)
		}
		response := apiRequest(handler, "GET", "/api/grade-tracking/status", "", cookie)
		if !strings.Contains(response.Body.String(), `"initial_notification_pending":true`) {
			t.Fatal(response)
		}
		api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
			if strings.Contains(message.HTML, "<课程>") || !strings.Contains(message.HTML, "&lt;课程&gt;") || !strings.Contains(message.Body, "教务总 GPA：3.8000") {
				t.Fatal(message)
			}
			return nil
		}
		if err := api.deliverOneMail(ctx, session); err != nil {
			t.Fatal(err)
		}
		if delivered, err := api.database.MailDelivered(ctx, session, "grade_tracking", "initial:"+activation); err != nil || !delivered {
			t.Fatal(delivered, err)
		}
		response = apiRequest(handler, "GET", "/api/grade-tracking/status", "", cookie)
		if !strings.Contains(response.Body.String(), `"initial_notification_pending":false`) {
			t.Fatal(response)
		}
		if response := apiRequest(handler, "PATCH", endpoint, `{"enabled":false}`, cookie); response.Code != 200 {
			t.Fatal(response)
		}
	}
}

func TestTrackingDisableDuringFetchDoesNotCommitOrNotify(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.accounts.mutex.Lock()
	api.accounts.automationTick, api.accounts.mailTick = nil, nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	cookie := loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(ctx, "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	apiRequest(handler, "PUT", "/api/system-settings/mail", `{"smtp_host":"smtp.example.invalid","from_email":"sender@example.invalid","to_email":"recipient@example.invalid"}`, cookie)
	apiRequest(handler, "PATCH", "/api/grade-tracking/enabled", `{"enabled":true}`, cookie)
	started, release := make(chan struct{}), make(chan struct{})
	api.resources.Register("scores", ResourceSpec{Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		close(started)
		select {
		case <-release:
		case <-ctx.Done():
			return nil, nil, ctx.Err()
		}
		return json.RawMessage(`{"scores":[]}`), nil, nil
	}})
	finished := make(chan error, 1)
	go func() { _, err := api.checkTrackingScores(ctx, session); finished <- err }()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("check did not start")
	}
	response := apiRequest(handler, "PATCH", "/api/grade-tracking/enabled", `{"enabled":false}`, cookie)
	close(release)
	if response.Code != 200 {
		t.Fatal(response)
	}
	if err := <-finished; !errors.Is(err, store.ErrConflict) {
		t.Fatal(err)
	}
	if _, err := api.database.Read(ctx, session.Account, gradeTrackingNamespace, "snapshot"); !errors.Is(err, store.ErrMissing) {
		t.Fatal(err)
	}
	if count, _ := api.database.PendingMailCount(ctx, session, "grade_tracking"); count != 0 {
		t.Fatal(count)
	}
}

func TestTrackingManualCheckUsesSharedCachePreservesSnapshotOnFailure(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	api.database.AddAccount(context.Background(), "student-a")
	cookie := loginCookie(t, handler, "student-a")
	score := academic.Score{Code: "A", Name: "课程", Term: "2026-2027-1", Score: "80", GPA: 3, Credit: 2, DetailRef: "private-ref"}
	failed := false
	calls := 0
	api.resources.Register("scores", ResourceSpec{Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		calls++
		if failed {
			return nil, nil, errors.New("private upstream failure")
		}
		scores := academic.Scores{Scores: []academic.Score{score}}
		payload, _ := json.Marshal(scores)
		semantic, _ := json.Marshal(scores.Semantic())
		return payload, semantic, nil
	}})
	endpoint := "/api/grade-tracking/check"
	if response := apiRequest(handler, "POST", endpoint, "", nil); response.Code != 401 || calls != 0 {
		t.Fatal(response)
	}
	response := apiRequest(handler, "POST", endpoint, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"initial":true`) || strings.Contains(response.Body.String(), "private-ref") {
		t.Fatal(response)
	}
	before, err := api.database.Read(context.Background(), "student-a", gradeTrackingNamespace, "snapshot")
	if err != nil {
		t.Fatal(err)
	}
	failed = true
	ageJobs := func() {
		api.resources.mutex.Lock()
		defer api.resources.mutex.Unlock()
		for _, job := range api.resources.jobs {
			job.UpdatedAt = time.Now().Add(-2 * time.Minute)
		}
	}
	ageJobs()
	response = apiRequest(handler, "POST", endpoint, "", cookie)
	if response.Code == 200 || strings.Contains(response.Body.String(), "private upstream failure") {
		t.Fatal(response)
	}
	after, _ := api.database.Read(context.Background(), "student-a", gradeTrackingNamespace, "snapshot")
	if before.Revision != after.Revision {
		t.Fatal("failed fetch replaced baseline")
	}
	response = apiRequest(handler, "GET", "/api/grade-tracking/status", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"stage":"error"`) || !strings.Contains(response.Body.String(), `"last_success_at":"`) {
		t.Fatal(response)
	}
	failed = false
	ageJobs()
	score.Score = "85"
	score.GPA = 3.5
	response = apiRequest(handler, "POST", endpoint, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"last_change_count":1`) || !strings.Contains(response.Body.String(), `"initial":false`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"last_change_count":0`) {
		t.Fatal(response)
	}
	api.trackingChecks.Store("student-a", true)
	defer api.trackingChecks.Delete("student-a")
	beforeCalls := calls
	response = apiRequest(handler, "POST", endpoint, "", cookie)
	if response.Code != 409 || calls != beforeCalls {
		t.Fatal("duplicate check started", response)
	}
}
