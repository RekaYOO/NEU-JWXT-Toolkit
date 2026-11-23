package application

import (
	"context"
	"encoding/json"
	"fmt"
	"html"
	"math"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type jwxkMailMarket struct {
	Class, Course   string
	Capacity, Count float64
}

func mailMarketNumber(value any) (float64, bool) {
	number, err := strconv.ParseFloat(academic.Text(value), 64)
	return number, err == nil && !math.IsNaN(number) && !math.IsInf(number, 0) && number >= 0 && math.Trunc(number) == number
}

func jwxkTaskMarkets(task academic.JWXKAutomationTask) []jwxkMailMarket {
	rows := []jwxkMailMarket{}
	seen := map[string]bool{}
	add := func(class, course string, capacityValue, countValue any) {
		capacity, capacityOK := mailMarketNumber(capacityValue)
		count, countOK := mailMarketNumber(countValue)
		if class != "" && course != "" && capacityOK && countOK && !seen[class] {
			rows = append(rows, jwxkMailMarket{class, course, capacity, count})
			seen[class] = true
		}
	}
	if task.Type == "weight_strategy" {
		allowed := map[string]string{}
		for _, item := range task.Items {
			allowed[academic.Text(item["class_id"])] = academic.Text(item["course_code"])
		}
		for _, row := range jwxkArchiveRows(task.WeightStatus["recommendation"]) {
			class := academic.Text(row["class_id"])
			if course := allowed[class]; course != "" {
				add(class, course, row["current_capacity"], row["current_participant_count"])
			}
		}
	} else if task.Type == "vacancy_swap" {
		for _, group := range task.SwapGroups {
			state := academic.Object(task.SwapResults[group.ID])
			snapshot := academic.Object(state["last_participant_snapshot"])
			add(group.Target.Class, group.Target.Course, snapshot["capacity"], snapshot["selected_count"])
		}
	} else {
		for _, group := range task.Groups {
			state := academic.Object(task.GroupResults[group.ID])
			class := academic.Text(state["current_class_id"])
			for _, item := range task.Items {
				if item["plan_group_id"] == group.ID && academic.Text(item["class_id"]) == class {
					snapshot := academic.Object(state["last_participant_snapshot"])
					add(class, academic.Text(item["course_code"]), snapshot["capacity"], snapshot["selected_count"])
				}
			}
		}
	}
	return rows
}

func (api *API) notifyJWXKMarket(ctx context.Context, session store.Session, task academic.JWXKAutomationTask, settings academic.JWXKAutomationSettings) error {
	if task.Status == "cancelled" {
		return nil
	}
	for _, row := range jwxkTaskMarkets(task) {
		for _, event := range []string{"capacity", "over_capacity", "underfilled"} {
			active := false
			title, reason := "", ""
			switch event {
			case "capacity":
				if row.Capacity <= 0 {
					continue
				}
				active = row.Count >= row.Capacity
				title, reason = "关注课程人数达到容量", "课程从未满变为满员或超额。"
			case "over_capacity":
				if row.Capacity <= 0 {
					continue
				}
				active = row.Count > row.Capacity && (row.Count-row.Capacity)/row.Capacity >= settings.OverCapacityRatio
				title, reason = "关注课程达到超额阈值", fmt.Sprintf("人数重新达到超额提醒阈值 %.0f%%。", settings.OverCapacityRatio*100)
			case "underfilled":
				window := academic.Text(task.WeightStatus["active_window"])
				calculated, err := time.Parse(time.RFC3339Nano, academic.Text(task.WeightStatus["last_calculated_at"]))
				if task.Type != "weight_strategy" || window != "final_check" && window != "final_notice" || err != nil || time.Since(calculated) > 10*time.Minute || task.Execution["round_ended"] == true || row.Count >= 10 || !jwxkMailEnabled(settings, event) {
					continue
				}
				active = true
				title, reason = "关注课程开课风险提示", "结束前检查发现已投注人数不足10；这只是风险提示，不代表官方最终停开决定。"
			}
			signature := "under"
			if active {
				signature = "reached"
			}
			var payload json.RawMessage
			if active && jwxkMailEnabled(settings, event) {
				label := "已选人数"
				if task.Type == "weight_strategy" {
					label = "已投注人数"
				}
				body := fmt.Sprintf("%s\n课程：%s；%s %.0f / 容量 %.0f\n%s\n\n%s", title, row.Course, label, row.Count, row.Capacity, reason, jwxkTaskMailBody(task, title))
				message := notifications.MailMessage{Subject: "[NEU 教务工具箱] " + title, Body: body, HTML: "<div style=\"white-space:pre-wrap;overflow-wrap:anywhere\">" + html.EscapeString(body) + "</div>", Context: map[string]string{"batch_code": task.Batch, "task_id": task.ID, "event": event, "class_id": row.Class}}
				var err error
				payload, err = json.Marshal(message)
				if err != nil {
					return err
				}
			}
			var queued bool
			var err error
			scope := task.ID + "/" + event + "/" + row.Class
			if event == "underfilled" {
				queued, err = api.database.ObserveMailEvent(ctx, session, "course_selection", scope, signature, payload)
			} else {
				queued, err = api.database.ObserveMailTransition(ctx, session, "course_selection", scope, signature, payload)
			}
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
	}
	return nil
}
