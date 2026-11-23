package academic

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type JWXKAutomationSettings struct {
	StrategyScheduleMode     string  `json:"strategy_schedule_mode"`
	RebalanceSeconds         int     `json:"rebalance_seconds"`
	ForceFinalRebalance      bool    `json:"force_final_rebalance"`
	FinalCheckMinutes        int     `json:"final_check_minutes"`
	FinalNoticeMinutes       int     `json:"final_notice_minutes"`
	FinalNoticeLatestTime    string  `json:"final_notice_latest_time"`
	MailEnabled              bool    `json:"mail_enabled"`
	NotifyRoundStart         bool    `json:"notify_round_start"`
	NotifyRoundEnd           bool    `json:"notify_round_end"`
	NotifyFinalRebalance     bool    `json:"notify_final_rebalance"`
	NotifyCapacityTransition bool    `json:"notify_capacity_transition"`
	NotifyOverCapacity       bool    `json:"notify_over_capacity"`
	NotifyUnderfilledWarning bool    `json:"notify_underfilled_warning"`
	NotifyGrabResult         bool    `json:"notify_grab_result"`
	OverCapacityRatio        float64 `json:"over_capacity_ratio"`
}

var automationClock = regexp.MustCompile(`^(?:[01][0-9]|2[0-3]):[0-5][0-9]$`)

func DefaultJWXKAutomationSettings() JWXKAutomationSettings {
	return JWXKAutomationSettings{StrategyScheduleMode: "interval", RebalanceSeconds: 1800, ForceFinalRebalance: true, FinalCheckMinutes: 3, FinalNoticeMinutes: 5, FinalNoticeLatestTime: "23:00", OverCapacityRatio: 0.2}
}

func (settings *JWXKAutomationSettings) UnmarshalJSON(payload []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(payload, &fields); err != nil {
		return err
	}
	if fields == nil {
		return errors.New("automation settings must be an object")
	}
	for _, value := range fields {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return errors.New("automation setting cannot be null")
		}
	}
	type plain JWXKAutomationSettings
	parsed := plain(*settings)
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&parsed); err != nil {
		return err
	}
	*settings = JWXKAutomationSettings(parsed)
	return nil
}

func (settings JWXKAutomationSettings) Valid() bool {
	return (settings.StrategyScheduleMode == "interval" || settings.StrategyScheduleMode == "final_windows") && settings.RebalanceSeconds >= 600 && settings.RebalanceSeconds <= 86400 && settings.FinalCheckMinutes >= 1 && settings.FinalCheckMinutes <= 1440 && settings.FinalNoticeMinutes >= 1 && settings.FinalNoticeMinutes <= 1440 && automationClock.MatchString(settings.FinalNoticeLatestTime) && !math.IsNaN(settings.OverCapacityRatio) && !math.IsInf(settings.OverCapacityRatio, 0) && settings.OverCapacityRatio >= 0 && settings.OverCapacityRatio <= 10
}

func (settings JWXKAutomationSettings) FinalWindows(end time.Time) (time.Time, time.Time, error) {
	if !settings.Valid() || end.IsZero() {
		return time.Time{}, time.Time{}, errors.New("invalid final window settings")
	}
	end = end.In(time.FixedZone("Asia/Shanghai", 8*3600))
	notice := end.Add(-time.Duration(settings.FinalNoticeMinutes) * time.Minute)
	clock := strings.Split(settings.FinalNoticeLatestTime, ":")
	hour, _ := strconv.Atoi(clock[0])
	minute, _ := strconv.Atoi(clock[1])
	latest := time.Date(notice.Year(), notice.Month(), notice.Day(), hour, minute, 0, 0, notice.Location())
	if latest.Before(notice) {
		notice = latest
	}
	return notice, end.Add(-time.Duration(settings.FinalCheckMinutes) * time.Minute), nil
}
