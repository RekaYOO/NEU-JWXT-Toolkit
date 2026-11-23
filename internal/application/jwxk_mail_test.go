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

func setupJWXKMail(t *testing.T) (*API, store.Session, academic.JWXKAutomationTask) {
	t.Helper()
	api, _, _, session, id := selectionTaskSetup(t, &selectionTaskClient{})
	api.accounts.mutex.Lock()
	api.accounts.mailTick = nil
	api.accounts.mutex.Unlock()
	config := notifications.MailConfig{Host: "smtp.example.invalid", Port: 465, Security: "ssl", From: "from@example.invalid", To: "to@example.invalid"}
	payload, _ := json.Marshal(config)
	if err := api.database.SaveSecretForSession(context.Background(), session, "mail-config", payload); err != nil {
		t.Fatal(err)
	}
	settings := academic.DefaultJWXKAutomationSettings()
	settings.MailEnabled = true
	settings.NotifyGrabResult = true
	payload, _ = json.Marshal(settings)
	if _, err := api.database.SaveForSession(context.Background(), session, jwxkAutomationSettingsNamespace, "round", payload, 0, false); err != nil {
		t.Fatal(err)
	}
	task := readSelectionTask(t, api, session, id)
	task.Status, task.Desired = "waiting", "running"
	return api, session, task
}

func TestJWXKMailAuthTransitionsDeduplicateAndInvalidateOldAlerts(t *testing.T) {
	api, session, task := setupJWXKMail(t)
	ctx := context.Background()
	sends := 0
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		sends++
		if !strings.Contains(message.Body, "恢复选课系统登录") || strings.Contains(message.Body, "已投注人数") {
			t.Fatal(message.Body)
		}
		return nil
	}
	observe := func(kind string) {
		task.Execution["last_error_type"] = kind
		if err := api.saveAutomationTask(ctx, session, &task); err != nil {
			t.Fatal(err)
		}
		if err := api.notifyJWXKTask(ctx, session, task); err != nil {
			t.Fatal(err)
		}
	}
	observe("SERVICE_AUTH_REQUIRED")
	observe("SERVICE_AUTH_REQUIRED")
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 1 {
		t.Fatal("same auth episode duplicated", count)
	}
	observe("")
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	if sends != 0 {
		t.Fatal("recovered login still sent stale alert")
	}
	observe("SERVICE_AUTH_REQUIRED")
	if err := api.deliverOneMail(ctx, session); err != nil || sends != 1 {
		t.Fatal(err, sends)
	}
	observe("SERVICE_AUTH_REQUIRED")
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 0 {
		t.Fatal("delivered auth episode repeated")
	}
}

func TestJWXKMailSettingsAndCancellationAreCheckedBeforeDelivery(t *testing.T) {
	for _, cancelled := range []bool{false, true} {
		api, session, task := setupJWXKMail(t)
		ctx := context.Background()
		task.GroupResults["g"] = map[string]any{"status": "success", "success_count": 1, "target_count": 1, "confirmed_course_codes": []string{"C1"}, "message": "官方结果确认"}
		api.saveAutomationTask(ctx, session, &task)
		if err := api.notifyJWXKTask(ctx, session, task); err != nil {
			t.Fatal(err)
		}
		if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 1 {
			t.Fatal(count)
		}
		if cancelled {
			task.Status, task.Desired = "cancelled", "cancelled"
			api.saveAutomationTask(ctx, session, &task)
		} else {
			settings, document, err := api.readJWXKAutomationSettings(ctx, session, task.Batch)
			if err != nil {
				t.Fatal(err)
			}
			settings.MailEnabled = false
			payload, _ := json.Marshal(settings)
			api.database.SaveForSession(ctx, session, jwxkAutomationSettingsNamespace, task.Batch, payload, document.Revision, false)
		}
		api.sendMail = func(context.Context, notifications.MailConfig, notifications.MailMessage) error {
			t.Fatal("invalidated message sent")
			return nil
		}
		if err := api.deliverOneMail(ctx, session); err != nil {
			t.Fatal(err)
		}
	}
}

func TestJWXKCapacityAlertsRequireObservedCrossingAndRecoverBelowThreshold(t *testing.T) {
	api, session, task := setupJWXKMail(t)
	ctx := context.Background()
	settings, document, _ := api.readJWXKAutomationSettings(ctx, session, task.Batch)
	settings.NotifyGrabResult = false
	settings.NotifyCapacityTransition = true
	settings.NotifyOverCapacity = true
	payload, _ := json.Marshal(settings)
	api.database.SaveForSession(ctx, session, jwxkAutomationSettingsNamespace, task.Batch, payload, document.Revision, false)
	observe := func(count any) {
		task.GroupResults["g"] = map[string]any{"current_class_id": "class", "last_participant_snapshot": map[string]any{"capacity": 10, "selected_count": count}}
		if err := api.saveAutomationTask(ctx, session, &task); err != nil {
			t.Fatal(err)
		}
		if err := api.notifyJWXKTask(ctx, session, task); err != nil {
			t.Fatal(err)
		}
	}
	observe(12)
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 0 {
		t.Fatal("initial full snapshot produced crossing alert", count)
	}
	observe(8)
	observe(12)
	observe(13)
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 2 {
		t.Fatal("threshold crossing count", count)
	}
	sends := 0
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		sends++
		if strings.Contains(message.Body, "已投注人数") || strings.Contains(message.Body, "建议") {
			t.Fatal("selection alert exposed weight terminology", message.Body)
		}
		return nil
	}
	api.deliverOneMail(ctx, session)
	api.deliverOneMail(ctx, session)
	observe(8)
	observe(12)
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 2 || sends != 2 {
		t.Fatal("new crossing suppressed", count, sends)
	}
	observe(nil)
	api.deliverOneMail(ctx, session)
	api.deliverOneMail(ctx, session)
	if sends != 2 {
		t.Fatal("unknown current count delivered old threshold alert")
	}
}

func TestJWXKUnderfilledRiskUsesFinalWeightSnapshotAndBoundTargetsOnly(t *testing.T) {
	api, session, task := setupJWXKMail(t)
	ctx := context.Background()
	settings, document, _ := api.readJWXKAutomationSettings(ctx, session, task.Batch)
	settings.NotifyGrabResult = false
	settings.NotifyUnderfilledWarning = true
	payload, _ := json.Marshal(settings)
	api.database.SaveForSession(ctx, session, jwxkAutomationSettingsNamespace, task.Batch, payload, document.Revision, false)
	task.Type = "weight_strategy"
	task.WeightStatus = map[string]any{"active_window": "interval", "last_calculated_at": time.Now().UTC().Format(time.RFC3339Nano), "recommendation": []map[string]any{{"class_id": "class", "course_code": "C1", "current_capacity": 30, "current_participant_count": 9}, {"class_id": "unrelated", "course_code": "PRIVATE_OTHER", "current_capacity": 30, "current_participant_count": 1}}}
	observe := func() {
		api.saveAutomationTask(ctx, session, &task)
		if err := api.notifyJWXKTask(ctx, session, task); err != nil {
			t.Fatal(err)
		}
	}
	observe()
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 0 {
		t.Fatal("risk alert outside final window")
	}
	task.WeightStatus["active_window"] = "final_check"
	observe()
	observe()
	if count, _ := api.database.PendingMailCount(ctx, session, "course_selection"); count != 1 {
		t.Fatal("risk warning duplicated or leaked audience", count)
	}
	sends := 0
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		sends++
		if strings.Contains(message.Body, "PRIVATE_OTHER") || !strings.Contains(message.Body, "不代表官方最终停开决定") {
			t.Fatal(message.Body)
		}
		return nil
	}
	if err := api.deliverOneMail(ctx, session); err != nil || sends != 1 {
		t.Fatal(err, sends)
	}
}
