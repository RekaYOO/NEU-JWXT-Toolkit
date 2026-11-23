package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestTrackingDetailsAreSelectiveAndMailPreservesBothFormats(t *testing.T) {
	api, session := setupMailQueue(t)
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	config := academic.DefaultGradeTrackingConfig()
	config.Enabled = true
	encoded, _ := json.Marshal(config)
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "config", encoded, 0, false)
	state := map[string]any{"activation_id": "activation", "generation": session.Generation}
	scores := academic.Scores{Scores: []academic.Score{{Code: "A", Term: "2026-1", Name: "A", Score: "80", GPA: 3, Credit: 2}}}
	calls := []string{}
	api.resources.Register("score-details", ResourceSpec{Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		calls = append(calls, variant)
		payload := json.RawMessage(`{"source_score":"90","source_gpa":4,"item_scores":[{"name":"<期末>","value":95}]}`)
		return payload, nil, nil
	}})
	commit := func() {
		t.Helper()
		current, document, err := api.readTrackingState(ctx, session)
		if err != nil {
			t.Fatal(err)
		}
		if document.Revision == 0 {
			current = state
		}
		if _, err := api.commitTrackingScores(ctx, session, config, current, document, scores); err != nil {
			t.Fatal(err)
		}
	}
	commit()
	if len(calls) != 0 {
		t.Fatal("baseline fetched details")
	}
	api.sendMail = func(context.Context, notifications.MailConfig, notifications.MailMessage) error { return nil }
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	scores.Scores[0].Name = "改名"
	commit()
	if len(calls) != 0 {
		t.Fatal("name change fetched details")
	}
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	scores.Scores[0].Score, scores.Scores[0].GPA = "90", 4
	commit()
	if len(calls) != 1 || calls[0] != scoreDetailVariant("A", "2026-1") {
		t.Fatal(calls)
	}
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		if !strings.Contains(message.Body, "<期末>：95") || !strings.Contains(message.HTML, "&lt;期末&gt;：95") {
			t.Fatal(message)
		}
		return nil
	}
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	commit()
	if len(calls) != 1 {
		t.Fatal("unchanged scores fetched details")
	}
	if count, _ := api.database.PendingMailCount(ctx, session, "grade_tracking"); count != 0 {
		t.Fatal(count)
	}
	if last, err := api.database.LastMailDelivery(ctx, session, "grade_tracking"); err != nil || last == "" {
		t.Fatal(last, err)
	}
	api.database.SetEnabled(ctx, session.Account, true)
	generation, _ := api.database.Generation(ctx, session.Account)
	current, _, err := api.readGradeTrackingConfig(ctx, store.Session{Account: session.Account, Generation: generation})
	if err != nil || current.Enabled {
		t.Fatal("new login reused old activation", current, err)
	}
}

func TestTrackingConsumesSharedScoresCommitWithoutAnotherFetch(t *testing.T) {
	api, session := setupMailQueue(t)
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	config := academic.DefaultGradeTrackingConfig()
	config.Enabled = true
	payload, _ := json.Marshal(config)
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "config", payload, 0, false)
	payload, _ = json.Marshal(map[string]any{"activation_id": "shared", "generation": session.Generation})
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", payload, 0, false)
	payload = json.RawMessage(`{"scores":[]}`)
	api.database.CommitCache(ctx, session, store.CacheEntry{Resource: "scores", Variant: "default", SchemaVersion: 1, Payload: payload, Revision: "shared"}, "manual", nil)
	api.afterScoresRefresh(session, payload, "manual")
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	for {
		if count, _ := api.database.PendingMailCount(ctx, session, "grade_tracking"); count == 1 {
			break
		}
		select {
		case <-deadline.C:
			t.Fatal("shared commit not consumed")
		case <-ticker.C:
		}
	}
	api.resources.mutex.Lock()
	defer api.resources.mutex.Unlock()
	for _, job := range api.resources.jobs {
		if job.Resource == "scores" {
			t.Fatal("observer fetched scores again")
		}
	}
}

func TestTrackingSchedulerInitialOutsideWindowAndFailureBackoff(t *testing.T) {
	api, session := setupMailQueue(t)
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	config := academic.DefaultGradeTrackingConfig()
	config.Enabled = true
	hour := time.Now().In(time.FixedZone("school", 8*3600)).Hour()
	config.StartHour, config.EndHour = (hour+1)%24, (hour+1)%24+1
	payload, _ := json.Marshal(config)
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "config", payload, 0, false)
	payload, _ = json.Marshal(map[string]any{"activation_id": "outside", "generation": session.Generation})
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", payload, 0, false)
	api.resources.Register("scores", ResourceSpec{Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		return nil, nil, context.DeadlineExceeded
	}})
	api.scheduleGradeTracking(ctx, session)
	api.resources.mutex.Lock()
	identifier := ""
	for _, job := range api.resources.jobs {
		if job.Resource == "grade-tracking" {
			identifier = job.ID
		}
	}
	api.resources.mutex.Unlock()
	if identifier == "" {
		t.Fatal("initial check suppressed outside window")
	}
	job, _ := api.resources.Job(session, identifier)
	select {
	case <-job.done:
	case <-time.After(2 * time.Second):
		t.Fatal("initial job blocked")
	}
	state, _, err := api.readTrackingState(ctx, session)
	if err != nil || state["stage"] != "error" {
		t.Fatal(state, err)
	}
	next, err := time.Parse(time.RFC3339Nano, academic.Text(state["next_check_at"]))
	if err != nil || !next.After(time.Now().Add(29*time.Minute)) {
		t.Fatal("failure has no backoff", state)
	}
	api.resources.mutex.Lock()
	before := len(api.resources.jobs)
	api.resources.mutex.Unlock()
	api.scheduleGradeTracking(ctx, session)
	api.resources.mutex.Lock()
	defer api.resources.mutex.Unlock()
	if len(api.resources.jobs) != before {
		t.Fatal("scheduler ignored backoff")
	}
}

func TestTrackingAuthMailDeduplicatesAndInvalidatesAfterRecovery(t *testing.T) {
	api, session := setupMailQueue(t)
	api.accounts.mutex.Lock()
	api.accounts.automationTick = nil
	api.accounts.mutex.Unlock()
	ctx := context.Background()
	config := academic.DefaultGradeTrackingConfig()
	config.Enabled = true
	payload, _ := json.Marshal(config)
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "config", payload, 0, false)
	state := map[string]any{"activation_id": "auth", "generation": session.Generation, "stage": "waiting_login"}
	payload, _ = json.Marshal(state)
	document, _ := api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", payload, 0, false)
	for attempt := 0; attempt < 2; attempt++ {
		if err := api.notifyTrackingAuth(ctx, session, state); err != nil {
			t.Fatal(err)
		}
	}
	if count, _ := api.database.PendingMailCount(ctx, session, "grade_tracking"); count != 1 {
		t.Fatal(count)
	}
	state["stage"] = "monitoring"
	payload, _ = json.Marshal(state)
	document, _ = api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", payload, document.Revision, false)
	if err := api.notifyTrackingAuth(ctx, session, state); err != nil {
		t.Fatal(err)
	}
	sends := 0
	api.sendMail = func(context.Context, notifications.MailConfig, notifications.MailMessage) error { sends++; return nil }
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	if sends != 0 {
		t.Fatal("sent stale auth alert")
	}
	state["stage"] = "waiting_login"
	payload, _ = json.Marshal(state)
	api.database.SaveForSession(ctx, session, gradeTrackingNamespace, "state", payload, document.Revision, false)
	if err := api.notifyTrackingAuth(ctx, session, state); err != nil {
		t.Fatal(err)
	}
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	if sends != 1 {
		t.Fatal("new auth failure did not notify", sends)
	}
}
