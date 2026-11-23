package application

import (
	"context"
	"encoding/json"
	"fmt"
	"html"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) trackingDetails(ctx context.Context, session store.Session, courses []academic.Score) []string {
	lines := []string{}
	for _, score := range courses {
		if ctx.Err() != nil {
			break
		}
		label := score.Name + "（" + score.Code + "，" + score.Term + "）分项："
		variant := scoreDetailVariant(score.Code, score.Term)
		result, err := api.resources.Submit(ctx, session, "score-details", variant, "tracking", true)
		if err != nil {
			lines = append(lines, label+"查询失败，总成绩不受影响")
			continue
		}
		job, exists := api.resources.Job(session, academic.Text(result["job_id"]))
		if !exists {
			lines = append(lines, label+"查询暂不可用，总成绩不受影响")
			continue
		}
		select {
		case <-job.done:
		case <-ctx.Done():
			return lines
		}
		job, _ = api.resources.Job(session, job.ID)
		if job.Status != "completed" {
			lines = append(lines, label+"查询失败，总成绩不受影响")
			continue
		}
		entry, err := api.database.ReadCache(ctx, session, "score-details", variant)
		var detail map[string]any
		if err != nil || json.Unmarshal(entry.Payload, &detail) != nil || detail["source_score"] != score.Score || academic.Number(detail["source_gpa"]) != score.GPA {
			lines = append(lines, label+"暂无匹配当前成绩的分项")
			continue
		}
		items, _ := detail["item_scores"].([]any)
		if len(items) == 0 {
			lines = append(lines, label+"暂无分项")
			continue
		}
		lines = append(lines, label)
		for _, item := range items {
			row := academic.Object(item)
			lines = append(lines, academic.Text(row["name"])+"："+academic.Text(row["value"]))
		}
	}
	return lines
}

func appendTrackingDetails(message *notifications.MailMessage, lines []string) {
	if len(lines) == 0 {
		return
	}
	message.Body += "\n\n" + strings.Join(lines, "\n")
	message.HTML = "<div>" + strings.ReplaceAll(html.EscapeString(message.Body), "\n", "<br>") + "</div>"
}

func (api *API) notifyTrackingAuth(ctx context.Context, session store.Session, state map[string]any) error {
	activation := academic.Text(state["activation_id"])
	if activation == "" {
		return nil
	}
	signature := "healthy"
	var payload json.RawMessage
	if state["stage"] == "waiting_login" {
		config, err := api.readMailConfig(ctx, session)
		if err != nil || !config.Valid(true) {
			return err
		}
		signature = "waiting_login"
		body := "成绩追踪暂时无法继续，教务登录已失效。请打开工具箱，手动恢复教务登录后继续追踪。上次成绩快照仍保留，本邮件不会替你开始扫码或发送短信。"
		message := notifications.MailMessage{Subject: "成绩追踪：教务登录需要恢复", Body: body, HTML: "<p>" + html.EscapeString(body) + "</p>", Context: map[string]string{"activation_id": activation, "event": "auth"}}
		identifier, err := api.attachRecoveryContext(ctx, session, "primary", "grade_tracking", activation+"/auth", &message)
		if err != nil {
			return err
		}
		signature += identifier
		payload, _ = json.Marshal(message)
	}
	queued, err := api.database.ObserveMailEvent(ctx, session, "grade_tracking", activation+"/auth", signature, payload)
	if err == nil && queued {
		select {
		case api.accounts.mailWake <- struct{}{}:
		default:
		}
	}
	return err
}

func trackingMail(current, previous academic.GradeTrackingSnapshot, change academic.GradeTrackingChange, policy map[string]any, activation string, initial bool) notifications.MailMessage {
	subject := "成绩追踪：成绩更新"
	lines := []string{}
	formatScore := func(score academic.Score) string {
		return fmt.Sprintf("%s（%s，%s）：成绩 %s，绩点 %.2f，学分 %.2f", score.Name, score.Code, score.Term, score.Score, score.GPA, score.Credit)
	}
	if initial {
		subject = "成绩追踪：初始同步"
		lines = append(lines, "本次开启追踪的当前成绩：")
		for _, score := range current.Scores.Scores {
			lines = append(lines, formatScore(score))
		}
	} else {
		for _, score := range change.Added {
			lines = append(lines, "新增："+formatScore(score))
		}
		for _, item := range change.Changed {
			lines = append(lines, "变更前："+formatScore(item["before"]), "变更后："+formatScore(item["after"]))
		}
		for _, score := range change.Removed {
			lines = append(lines, "移除："+formatScore(score))
		}
	}
	official := func(value *float64) string {
		if value == nil {
			return "教务暂无数据"
		}
		return fmt.Sprintf("%.4f", *value)
	}
	average, _, _ := gpaSummary(current.Scores.Scores, policy)
	mode := "2025级以前策略"
	if policy["mode"] == "from_2025" {
		mode = "2025级及以后策略"
	}
	lines = append(lines, "教务总 GPA："+official(current.Scores.OverallGPA), fmt.Sprintf("本地平均绩点（%s）：%.4f", mode, average))
	if !initial {
		oldAverage, _, _ := gpaSummary(previous.Scores.Scores, policy)
		lines = append(lines, "变更前教务总 GPA："+official(previous.Scores.OverallGPA), fmt.Sprintf("变更前本地平均绩点（使用同一当前策略）：%.4f", oldAverage))
	}
	if policy["mode"] == "from_2025" && (policy["report_available"] != true || academic.Number(policy["missing_grading_scales"]) > 0) {
		lines = append(lines, "培养计划或课程分类缓存尚不完整，本地绩点暂供参考。")
	}
	body := strings.Join(lines, "\n")
	return notifications.MailMessage{Subject: subject, Body: body, HTML: "<div>" + strings.ReplaceAll(html.EscapeString(body), "\n", "<br>") + "</div>", Context: map[string]string{"activation_id": activation}}
}
