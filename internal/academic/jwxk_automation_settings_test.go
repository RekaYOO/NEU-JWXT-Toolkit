package academic

import (
	"encoding/json"
	"testing"
	"time"
)

func TestAutomationSettingsRejectInvalidAndUnknownValues(t *testing.T) {
	for _, payload := range []string{`null`, `{"smtp_configured":true}`, `{"mail_enabled":null}`, `{"mail_enabled":"false"}`, `{"rebalance_seconds":0}`, `{"rebalance_seconds":599}`, `{"final_check_minutes":1441}`, `{"final_notice_minutes":0}`, `{"final_notice_latest_time":"24:00"}`, `{"final_notice_latest_time":"3:05"}`, `{"over_capacity_ratio":-1}`, `{"strategy_schedule_mode":"always"}`} {
		settings := DefaultJWXKAutomationSettings()
		if err := json.Unmarshal([]byte(payload), &settings); err == nil && settings.Valid() {
			t.Fatal("invalid configuration accepted", payload)
		}
	}
	settings := DefaultJWXKAutomationSettings()
	if err := json.Unmarshal([]byte(`{"mail_enabled":true,"notify_round_start":true,"over_capacity_ratio":0}`), &settings); err != nil || !settings.Valid() || !settings.MailEnabled || !settings.NotifyRoundStart || settings.RebalanceSeconds != 1800 || settings.OverCapacityRatio != 0 {
		t.Fatal(settings, err)
	}
}

func TestAutomationFinalWindowsUseSchoolTimezoneAcrossMidnight(t *testing.T) {
	settings := DefaultJWXKAutomationSettings()
	end := time.Date(2026, 11, 17, 16, 2, 0, 0, time.UTC)
	notice, check, err := settings.FinalWindows(end)
	if err != nil || notice.Format(time.RFC3339) != "2026-11-17T23:00:00+08:00" || check.Format(time.RFC3339) != "2026-11-17T23:59:00+08:00" {
		t.Fatal(notice, check, err)
	}
	end = time.Date(2026, 11, 17, 13, 0, 0, 0, time.UTC)
	notice, check, err = settings.FinalWindows(end)
	if err != nil || notice.Format(time.RFC3339) != "2026-11-17T20:55:00+08:00" || check.Format(time.RFC3339) != "2026-11-17T20:57:00+08:00" {
		t.Fatal(notice, check, err)
	}
}
