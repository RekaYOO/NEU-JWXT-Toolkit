package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"sort"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func jwxkMailEnabled(settings academic.JWXKAutomationSettings, event string) bool {
	if !settings.MailEnabled {
		return false
	}
	switch event {
	case "auth":
		return true
	case "round_start":
		return settings.NotifyRoundStart
	case "round_end":
		return settings.NotifyRoundEnd
	case "result", "cancelled", "overdue":
		return settings.NotifyGrabResult
	case "strategy":
		return settings.NotifyFinalRebalance
	case "capacity":
		return settings.NotifyCapacityTransition
	case "over_capacity":
		return settings.NotifyOverCapacity
	case "underfilled":
		return settings.NotifyUnderfilledWarning
	}
	return false
}

func (api *API) mailEntryValid(ctx context.Context, entry store.MailEntry, message notifications.MailMessage) (bool, error) {
	active, err := api.database.MailClaimActive(ctx, entry)
	if err != nil || !active {
		return false, err
	}
	if entry.Source == "grade_tracking" {
		config, _, err := api.readGradeTrackingConfig(ctx, entry.Session)
		if err != nil || !config.Enabled {
			return false, err
		}
		state, _, err := api.readTrackingState(ctx, entry.Session)
		if err != nil || message.Context["activation_id"] == "" || message.Context["activation_id"] != academic.Text(state["activation_id"]) {
			return false, err
		}
		if message.Context["event"] == "auth" {
			if state["stage"] != "waiting_login" {
				return false, nil
			}
			return api.database.MailEventCurrent(ctx, entry.Session, entry.Source, message.EventScope, message.EventRevision)
		}
		return true, nil
	}
	if entry.Source != "course_selection" {
		return true, nil
	}
	if message.EventScope != "" {
		current, err := api.database.MailEventCurrent(ctx, entry.Session, entry.Source, message.EventScope, message.EventRevision)
		if err != nil || !current {
			return false, err
		}
	}
	batch, taskID, event := message.Context["batch_code"], message.Context["task_id"], message.Context["event"]
	if batch == "" || taskID == "" {
		return false, nil
	}
	settings, _, err := api.readJWXKAutomationSettings(ctx, entry.Session, batch)
	if err != nil {
		return false, err
	}
	if !jwxkMailEnabled(settings, event) {
		return false, nil
	}
	document, err := api.database.Read(ctx, entry.Session.Account, jwxkAutomationTaskNamespace, taskID)
	if errors.Is(err, store.ErrMissing) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var task academic.JWXKAutomationTask
	if document.Deleted || json.Unmarshal(document.Payload, &task) != nil || task.Batch != batch {
		return false, nil
	}
	if task.Status == "cancelled" {
		return event == "cancelled", nil
	}
	if event == "cancelled" {
		return false, nil
	}
	if event == "round_start" && task.Execution["round_active"] != true {
		return false, nil
	}
	if event == "round_end" && task.Execution["round_ended"] != true {
		return false, nil
	}
	if event == "auth" {
		kind := academic.Text(task.Execution["last_error_type"])
		return strings.Contains(kind, "AUTH"), nil
	}
	if event == "capacity" || event == "over_capacity" || event == "underfilled" {
		for _, row := range jwxkTaskMarkets(task) {
			if row.Class != message.Context["class_id"] {
				continue
			}
			switch event {
			case "capacity":
				return row.Capacity > 0 && row.Count >= row.Capacity, nil
			case "over_capacity":
				return row.Capacity > 0 && row.Count > row.Capacity && (row.Count-row.Capacity)/row.Capacity >= settings.OverCapacityRatio, nil
			case "underfilled":
				return task.Type == "weight_strategy" && row.Count < 10 && task.Execution["round_ended"] != true, nil
			}
		}
		return false, nil
	}
	return true, nil
}

func jwxkTaskMailBody(task academic.JWXKAutomationTask, title string) string {
	lines := []string{title, "任务：" + task.Name, "轮次：" + task.Batch, "当前状态：" + task.Status, task.Message, ""}
	if task.Type == "vacancy_swap" {
		for _, group := range task.SwapGroups {
			state := academic.Object(task.SwapResults[group.ID])
			lines = append(lines, group.Name+"："+group.Target.Name+"（"+group.Target.Course+"）", "我的结果："+academic.Text(state["status"]), academic.Text(state["message"]))
			for _, drop := range group.Drop {
				lines = append(lines, "指定退课："+drop.Name+"（"+drop.Course+"）")
			}
		}
	} else {
		for _, group := range task.Groups {
			state := academic.Object(task.GroupResults[group.ID])
			lines = append(lines, fmt.Sprintf("方案组：%s；已确认 %.0f / %d 门", group.Name, academic.Number(state["success_count"]), group.Target))
			for _, item := range task.Items {
				if item["plan_group_id"] == group.ID {
					personal := "尚未核验"
					if state["confirmed_course_codes"] != nil {
						personal = "未选中"
					}
					for _, code := range jwxkMailStrings(state["confirmed_course_codes"]) {
						if strings.EqualFold(code, academic.Text(item["course_code"])) {
							personal = "已选中"
						}
					}
					if task.PendingSelection != nil && task.PendingSelection.Class == academic.Text(item["class_id"]) {
						personal = "待确认"
					}
					lines = append(lines, academic.Text(item["course_name"])+"（"+academic.Text(item["course_code"])+"）；我的结果："+personal)
				}
			}
			lines = append(lines, academic.Text(state["message"]))
		}
	}
	if task.Type == "weight_strategy" {
		allowed := map[string]bool{}
		for _, item := range task.Items {
			allowed[academic.Text(item["class_id"])] = true
		}
		for _, item := range jwxkArchiveRows(task.WeightStatus["recommendation"]) {
			if allowed[academic.Text(item["class_id"])] {
				lines = append(lines, fmt.Sprintf("%s：建议 %.0f 点；已投注人数 %.0f / 可选容量 %.0f", academic.Text(item["course_code"]), academic.Number(item["weight"]), academic.Number(item["current_participant_count"]), academic.Number(item["current_capacity"])))
			}
		}
		lines = append(lines, "模型代理值不是实际录取概率；实际结果以学校为准。")
	}
	lines = append(lines, "", "请在工具箱中查看最新官方结果。此邮件不会触发任何选课或退课操作。")
	return strings.Join(lines, "\n")
}

func (api *API) notifyJWXKTask(ctx context.Context, session store.Session, task academic.JWXKAutomationTask) error {
	current, err := api.database.Read(ctx, session.Account, jwxkAutomationTaskNamespace, task.ID)
	if err != nil {
		return err
	}
	if current.Deleted || current.Revision != task.Revision {
		return nil
	}
	settings, _, err := api.readJWXKAutomationSettings(ctx, session, task.Batch)
	if err != nil {
		return err
	}
	config, err := api.readMailConfig(ctx, session)
	if err != nil {
		return err
	}
	if !config.Valid(true) {
		return nil
	}
	type observation struct {
		scope, event, title, signature string
		active                         bool
	}
	observations := []observation{
		{"auth", "auth", "选课登录需要恢复", academic.Text(task.Execution["last_error_type"]), strings.Contains(academic.Text(task.Execution["last_error_type"]), "AUTH")},
		{"round_start", "round_start", "选课轮次已开放", "active", task.Execution["round_active"] == true},
		{"round_end", "round_end", "选课轮次结束总结", task.Status, task.Execution["round_ended"] == true},
		{"cancelled", "cancelled", "选课任务已取消", "cancelled", task.Status == "cancelled"},
		{"overdue", "overdue", "选课开放后仍有目标待完成", "overdue", task.Type != "weight_strategy" && task.Execution["round_overdue"] == true && task.Status != "success" && task.Desired == "running"},
		{"strategy", "strategy", "本轮投权策略已更新", academic.Text(task.WeightStatus["last_calculated_at"]), task.Type == "weight_strategy" && academic.Text(task.WeightStatus["last_calculated_at"]) != ""},
	}
	groups := task.GroupResults
	if task.Type == "vacancy_swap" {
		groups = task.SwapResults
	}
	if task.Type != "weight_strategy" {
		bound := map[string]bool{}
		for _, group := range task.Groups {
			bound[group.ID] = true
		}
		for _, group := range task.SwapGroups {
			bound[group.ID] = true
		}
		keys := []string{}
		for key := range groups {
			if bound[key] {
				keys = append(keys, key)
			}
		}
		sort.Strings(keys)
		for _, key := range keys {
			state := academic.Object(groups[key])
			status, candidate := academic.Text(state["status"]), academic.Text(state["candidate_status"])
			signature, _ := json.Marshal([]any{status, candidate, state["current_class_id"], state["success_count"], state["message"]})
			active := status == "success" || status == "needs_review" || status == "verifying" || status == "verifying_drop" || candidate != "" && candidate != "data_unknown" && candidate != "submitting"
			observations = append(observations, observation{"group/" + key, "result", "选课任务结果更新", string(signature), active})
		}
	}
	for _, observation := range observations {
		active := observation.active && jwxkMailEnabled(settings, observation.event) && (task.Status != "cancelled" || observation.event == "cancelled")
		signature := "inactive"
		var payload json.RawMessage
		if active {
			digest := sha256.Sum256([]byte(observation.signature))
			signature = hex.EncodeToString(digest[:])
			body := jwxkTaskMailBody(task, observation.title)
			if observation.event == "auth" {
				body += "\n请恢复选课系统登录；已有选退课结果仍需核验，不会因恢复登录而重复提交。"
			}
			message := notifications.MailMessage{Subject: "[NEU 教务工具箱] " + observation.title, Body: body, HTML: "<div style=\"white-space:pre-wrap;overflow-wrap:anywhere\">" + html.EscapeString(body) + "</div>", Context: map[string]string{"batch_code": task.Batch, "task_id": task.ID, "event": observation.event}}
			if observation.event == "auth" {
				identifier, err := api.attachRecoveryContext(ctx, session, "jwxk", "course_selection", task.ID+"/"+observation.scope, &message)
				if err != nil {
					return err
				}
				signature += identifier
			}
			payload, err = json.Marshal(message)
			if err != nil {
				return err
			}
		}
		queued, err := api.database.ObserveMailEvent(ctx, session, "course_selection", task.ID+"/"+observation.scope, signature, payload)
		if err != nil {
			return err
		}
		if queued {
			select {
			case api.accounts.mailWake <- struct{}{}:
			default:
			}
		}
	}
	return api.notifyJWXKMarket(ctx, session, task, settings)
}

func jwxkMailStrings(value any) []string {
	if values, ok := value.([]string); ok {
		return values
	}
	result := []string{}
	if values, ok := value.([]any); ok {
		for _, value := range values {
			result = append(result, academic.Text(value))
		}
	}
	return result
}
