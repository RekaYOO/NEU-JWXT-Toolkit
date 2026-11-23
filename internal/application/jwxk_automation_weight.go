package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func weightStrategyTrigger(task *academic.JWXKAutomationTask, settings academic.JWXKAutomationSettings, now, end time.Time) string {
	state := task.WeightStatus
	if settings.ForceFinalRebalance || settings.StrategyScheduleMode == "final_windows" {
		notice, check, err := settings.FinalWindows(end)
		if err == nil {
			if settings.ForceFinalRebalance && !now.Before(check) && state["final_check_executed"] != true {
				return "final_check"
			}
			if settings.StrategyScheduleMode == "final_windows" && !now.Before(notice) && state["final_notice_executed"] != true {
				return "final_notice"
			}
		}
	}
	last, err := time.Parse(time.RFC3339Nano, academic.Text(state["last_strategy_at"]))
	if err != nil || state["retry_strategy"] == true {
		return "interval"
	}
	if now.Sub(last) >= time.Duration(settings.RebalanceSeconds)*time.Second && (settings.StrategyScheduleMode == "interval" || task.ManualCheck != "") {
		return "interval"
	}
	return ""
}

func (api *API) finishWeightTask(ctx context.Context, runtime *Runtime, task *academic.JWXKAutomationTask, mode string) error {
	official, err := academic.FetchJWXKSelected(ctx, runtime, mode, task.Batch)
	if err != nil {
		return err
	}
	if official["complete"] != true || official["term_code"] != task.Term || official["selection_type_code"] != "04" {
		return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "轮次结束结果尚未完整核验"}
	}
	codes := map[string]bool{}
	for _, feed := range []string{"selected", "volunteered"} {
		for _, row := range jwxkArchiveRows(official[feed]) {
			if row["current_batch_record"] == true || feed == "selected" && academic.Text(row["record_term_code"]) == task.Term {
				codes[strings.ToUpper(academic.Text(row["course_code"]))] = true
			}
		}
	}
	allDone := len(task.Groups) > 0
	for _, group := range task.Groups {
		matched := map[string]bool{}
		for _, row := range task.Items {
			code := strings.ToUpper(academic.Text(row["course_code"]))
			if row["plan_group_id"] == group.ID && codes[code] {
				matched[code] = true
			}
		}
		state := "success"
		if len(matched) < group.Target {
			state, allDone = "failed", false
		}
		if task.GroupResults == nil {
			task.GroupResults = map[string]any{}
		}
		task.GroupResults[group.ID] = map[string]any{"status": state, "target_count": group.Target, "success_count": len(matched)}
	}
	task.Status, task.Desired, task.Message = "failed", "paused", "轮次已结束，官方结果尚未满足全部方案组目标"
	if allDone {
		task.Status, task.Message = "success", "轮次已结束，官方结果已覆盖全部方案组目标"
	}
	return nil
}

func (api *API) tickJWXKWeightStep(ctx context.Context, runtime *Runtime, session store.Session, task *academic.JWXKAutomationTask, mode string, batch map[string]any, now time.Time, ended bool) error {
	if task.WeightStatus == nil {
		task.WeightStatus = map[string]any{}
	}
	state := task.WeightStatus
	settings, _, err := api.readJWXKAutomationSettings(ctx, session, task.Batch)
	if err != nil {
		return err
	}
	task.RebalanceSeconds = settings.RebalanceSeconds
	task.NextAttempt = time.Now().UTC().Add(15 * time.Second).Format(time.RFC3339Nano)
	start, startError := academic.ParseJWXKTaskTime(task.Start)
	canWrite := !ended && batch["can_enter"] == true && (startError != nil || !now.Before(start))
	if task.WeightApplication != nil {
		result, err := api.advanceJWXKWeights(ctx, runtime, session, mode, *task.WeightApplication, 1, !canWrite)
		if err != nil {
			return err
		}
		state["phase"], state["last_results"] = result["phase"], result["results"]
		if err := api.updateWeightTaskProgress(ctx, session, task, result); err != nil {
			return err
		}
		if result["completed"] == true {
			if window := academic.Text(state["active_window"]); window != "" && window != "interval" {
				state[window+"_executed"] = true
			}
			task.WeightApplication = nil
			state["retry_strategy"], state["phase"] = false, "idle"
			state["last_adjusted_at"] = time.Now().UTC().Format(time.RFC3339Nano)
			task.Status, task.Message = "waiting", "本次策略已由官方结果确认，等待下一次检查"
		} else if result["yielded"] == true {
			task.Status, task.Message = "waiting", "当前投权步骤已确认，下一次检查继续处理"
			return nil
		} else {
			task.Status, task.Desired, task.Message = "needs_review", "paused", "投权步骤未完成，请核验原方案后继续"
			if results, ok := result["results"].([]weightApplyResult); ok && len(results) > 0 {
				task.Message = results[len(results)-1].Message
			}
			if results, ok := result["results"].([]weightApplyResult); ended && ok && len(results) == 0 {
				task.Status, task.Message = "failed", "轮次已结束，未发送的投权步骤已停止"
			}
			return nil
		}
	}
	if ended {
		return api.finishWeightTask(ctx, runtime, task, mode)
	}
	if !canWrite {
		task.Status, task.Message = "waiting", "等待权重轮次开放与账号资格核验"
		return nil
	}
	if task.WeightScanJob == "" {
		end, _ := academic.ParseJWXKTaskTime(academic.Text(batch["end_time"]))
		if configured, err := academic.ParseJWXKTaskTime(task.End); err == nil && (end.IsZero() || configured.Before(end)) {
			end = configured
		}
		trigger := weightStrategyTrigger(task, settings, now, end)
		if trigger == "" {
			task.NextAttempt = time.Now().UTC().Add(time.Minute).Format(time.RFC3339Nano)
			task.Status, task.Message = "waiting", "等待策略间隔或临近结束检查窗口"
			return nil
		}
		document, archive, err := api.readJWXKArchive(ctx, session, task.Batch)
		if err != nil {
			return err
		}
		if document.Deleted {
			return store.ErrMissing
		}
		if archive == nil {
			encoded, _ := json.Marshal(map[string]any{"archive_id": jwxkArchiveID(task.Batch), "batch_code": task.Batch, "term_code": task.Term, "selection_type_code": "04", "courses": []any{}})
			if _, err := api.database.SaveForSession(ctx, session, jwxkArchiveNamespace, jwxkArchiveID(task.Batch), encoded, document.Revision, false); err != nil {
				return err
			}
		}
		id, err := api.resources.startDocumentWork(session, "jwxk-catalog", task.Batch, func(ctx context.Context, runtime *Runtime) error {
			return api.scanJWXKCatalog(ctx, session, runtime, task.Batch, mode)
		})
		if err != nil {
			return err
		}
		task.WeightScanJob = id
		state["active_window"], state["last_strategy_at"], state["retry_strategy"] = trigger, now.UTC().Format(time.RFC3339Nano), true
		state["phase"], task.Status, task.Message = "market_refresh", "waiting", "正在刷新当前轮次完整市场，完成后计算投权策略"
		return nil
	}
	job, exists := api.resources.Job(session, task.WeightScanJob)
	if !exists || job.Status == "failed" || job.Status == "cancelled" {
		task.WeightScanJob = ""
		return &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "市场刷新尚未完成，将重新获取完整市场"}
	}
	if job.Status != "completed" {
		task.Status, task.Message = "waiting", "等待完整市场刷新完成"
		return nil
	}
	_, archive, err := api.readJWXKArchive(ctx, session, task.Batch)
	if err != nil {
		return err
	}
	scan := academic.Text(archive["completed_scan_id"])
	refreshed, refreshError := time.Parse(time.RFC3339, academic.Text(archive["last_sync_at"]))
	rows := []map[string]any{}
	for _, row := range jwxkArchiveRows(archive["courses"]) {
		if scan != "" && row["market_scan_id"] == scan {
			rows = append(rows, row)
		}
	}
	if refreshError != nil || time.Since(refreshed) > time.Minute || archive["sync_status"] != "complete" || archive["term_code"] != task.Term || archive["selection_type_code"] != "04" || len(rows) == 0 || len(rows) != int(academic.Number(archive["completed_course_count"])) {
		task.WeightScanJob = ""
		return &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "市场快照已变化，将重新刷新"}
	}
	official, err := academic.FetchJWXKSelected(ctx, runtime, mode, task.Batch)
	if err != nil {
		return err
	}
	budget, err := academic.FetchJWXKWeightBudget(ctx, runtime, mode, task.Batch)
	if err != nil {
		return err
	}
	select {
	case weightSolverSlots <- struct{}{}:
		defer func() { <-weightSolverSlots }()
	default:
		return &campus.Error{Code: "BUSY", Message: "策略计算繁忙，稍后继续"}
	}
	if task.GradeSize == nil {
		return store.ErrConflict
	}
	plan, err := academic.BuildJWXKWeightPlan(ctx, academic.JWXKWeightPlan{JWXKSavedPlan: task.JWXKSavedPlan, GradeSize: *task.GradeSize}, rows, official, budget)
	if err != nil {
		task.WeightScanJob = ""
		return err
	}
	task.WeightScanJob = ""
	state["recommendation"], state["warnings"], state["market_scan_id"] = plan["courses"], plan["warnings"], scan
	state["last_calculated_at"] = time.Now().UTC().Format(time.RFC3339Nano)
	items := jwxkArchiveRows(plan["items"])
	if len(items) == 0 {
		state["retry_strategy"], state["phase"] = false, "idle"
		if window := academic.Text(state["active_window"]); window != "" && window != "interval" {
			state[window+"_executed"] = true
		}
		task.Message = "当前模型没有需要调整的投权目标，等待下一次检查"
		return nil
	}
	application := academic.JWXKSavedPlan{Batch: task.Batch, Term: task.Term, Items: items}
	if err := api.prepareWeightTaskCycle(ctx, session, application, official); err != nil {
		task.Status, task.Desired, task.Message = "needs_review", "paused", "存在其他未完成投权方案，请先核验再继续策略任务"
		return nil
	}
	task.WeightApplication = &application
	state["phase"], task.Status, task.Message = "prepared", "waiting", "最新策略已保存，下一次检查逐步撤回并投放"
	return nil
}

func (api *API) updateWeightTaskProgress(ctx context.Context, session store.Session, task *academic.JWXKAutomationTask, result map[string]any) error {
	encoded, err := json.Marshal(result["results"])
	if err != nil {
		return err
	}
	rows := []map[string]any{}
	if err := json.Unmarshal(encoded, &rows); err != nil {
		return err
	}
	for _, row := range rows {
		row["at"] = time.Now().UTC().Format(time.RFC3339Nano)
		task.Results = append(task.Results, row)
	}
	if len(task.Results) > 80 {
		task.Results = task.Results[len(task.Results)-80:]
	}
	document, err := api.database.Read(ctx, session.Account, jwxkWeightApplicationNamespace, task.Batch)
	if err != nil {
		return err
	}
	var application weightApplication
	if document.Deleted || json.Unmarshal(document.Payload, &application) != nil {
		return store.ErrConflict
	}
	drops, additions := []map[string]any{}, []map[string]any{}
	task.WeightStatus["inflight"] = nil
	for _, item := range application.Items {
		key, action, class := "", "", ""
		if item.Removal != nil && !item.Withdrawn {
			key, action, class = jwxkDeselectionKey(task.Batch, item.Removal.Class), "drop", item.Removal.Class
			drops = append(drops, map[string]any{"class_id": class, "course_code": item.Selection.Course})
		} else if !item.Done && !item.WithdrawOnly {
			key, action, class = jwxkSelectionKey(item.Selection), "add", item.Selection.Class
			additions = append(additions, map[string]any{"class_id": class, "course_code": item.Selection.Course, "weight": item.Selection.Weight})
		}
		if key == "" {
			continue
		}
		attempt, err := api.database.SelectionAttempt(ctx, session, key)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return err
		}
		if err == nil && (attempt.State == "pending" || attempt.State == "unknown") {
			task.WeightStatus["inflight"] = map[string]any{"action": action, "class_id": class, "course_code": item.Selection.Course, "weight": item.Selection.Weight}
		}
	}
	task.WeightStatus["pending_drop"], task.WeightStatus["pending_add"] = drops, additions
	return nil
}

func (api *API) prepareWeightTaskCycle(ctx context.Context, session store.Session, input academic.JWXKSavedPlan, official map[string]any) error {
	if official["complete"] != true || official["term_code"] != input.Term || official["selection_type_code"] != "04" {
		return store.ErrConflict
	}
	items, _, err := weightApplyInputs(input)
	if err != nil {
		return err
	}
	document, err := api.database.Read(ctx, session.Account, jwxkWeightApplicationNamespace, input.Batch)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return err
	}
	if err == nil {
		var previous weightApplication
		if document.Deleted || json.Unmarshal(document.Payload, &previous) != nil || previous.Phase != "complete" && previous.Phase != "cancelled" {
			return store.ErrConflict
		}
		if previous.Phase == "complete" {
			previous.Phase, previous.Revision = "cancelled", document.Revision
			if err := api.saveWeightApplication(ctx, session, input.Batch, &previous); err != nil {
				return err
			}
		}
	}
	present := map[string]bool{}
	for _, feed := range []string{"volunteered", "selected"} {
		for _, row := range jwxkArchiveRows(official[feed]) {
			present[strings.ToUpper(academic.Text(row["course_code"]))] = true
		}
	}
	for _, item := range items {
		if !present[item.Course] {
			if err := api.rearmConfirmedSelectionOperation(ctx, session, jwxkSelectionKey(item)); err != nil {
				return err
			}
		}
	}
	return nil
}
