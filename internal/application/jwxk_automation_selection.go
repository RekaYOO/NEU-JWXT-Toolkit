package application

import (
	"context"
	"encoding/json"
	"errors"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) tickJWXKSelectionTask(ctx context.Context, runtime *Runtime, session store.Session, id string) (tickError error) {
	if runtime == nil || api.accounts.Runtime(session) != runtime || runtime.Context.Err() != nil {
		return store.ErrAccess
	}
	document, err := api.database.Read(ctx, session.Account, jwxkAutomationTaskNamespace, id)
	if err != nil {
		return err
	}
	var task academic.JWXKAutomationTask
	if document.Deleted || json.Unmarshal(document.Payload, &task) != nil {
		return store.ErrConflict
	}
	if task.Desired != "running" || task.Status != "running" && task.Status != "waiting" {
		return nil
	}
	now := time.Now().UTC()
	if next, err := time.Parse(time.RFC3339Nano, task.NextAttempt); err == nil && now.Before(next) {
		return nil
	}
	task.Revision = document.Revision
	task.Attempts++
	task.LastAttempt = now.Format(time.RFC3339Nano)
	task.NextAttempt = now.Add(task.PollInterval()).Format(time.RFC3339Nano)
	if task.Execution == nil {
		task.Execution = map[string]any{}
	}
	task.Execution["state"], task.Execution["stage"], task.Execution["stage_code"] = "running", "读取官方轮次与结果", "official_state"
	task.Execution["check_started_at"] = task.LastAttempt
	task.Execution["last_error_type"], task.Execution["last_error_message"] = "", ""
	if err := api.saveAutomationTask(ctx, session, &task); err != nil {
		return err
	}
	defer func() {
		task.ManualCheck = ""
		if tickError != nil {
			if task.PendingSelection != nil {
				if may, err := selectionTaskMayReconcile(ctx, api, session, *task.PendingSelection); err == nil && !may {
					task.PendingSelection = nil
					task.PendingGroup = ""
				}
			}
			task.Status, task.Message = "waiting", "学校状态暂时无法核验，将稍后重试"
			task.NextAttempt = time.Now().UTC().Add(time.Minute).Format(time.RFC3339Nano)
			kind := "UNKNOWN"
			var failure *campus.Error
			if errors.As(tickError, &failure) {
				kind = failure.Code
				task.Message = failure.Message
			}
			task.Execution["last_error_type"], task.Execution["last_error_message"] = kind, task.Message
		}
		task.Execution["state"], task.Execution["last_completed_at"], task.Execution["last_duration_ms"] = "idle", time.Now().UTC().Format(time.RFC3339Nano), time.Since(now).Milliseconds()
		if tickError != nil {
			task.Execution["state"] = "error"
		}
		persist, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		defer cancel()
		if err := api.saveAutomationTask(persist, session, &task); tickError == nil {
			tickError = err
		}
		if tickError == nil {
			api.notifyJWXKTask(persist, session, task)
		}
	}()
	mode, _, err := api.jwxkPreference(ctx, session)
	if err != nil {
		return err
	}
	if mode == "follow" {
		_, mode = runtime.Client.Status()
	}
	contextData, err := academic.FetchJWXKContext(ctx, runtime, mode)
	if err != nil {
		return err
	}
	var batch map[string]any
	for _, candidate := range jwxkArchiveRows(contextData["batches"]) {
		if academic.Text(candidate["code"]) == task.Batch {
			batch = candidate
			break
		}
	}
	expectedMode := "02"
	if task.Type == "weight_strategy" {
		expectedMode = "04"
	}
	if batch == nil || batch["selection_type_code"] != expectedMode || batch["term_code"] != task.Term {
		task.Status, task.Desired, task.Message = "paused", "paused", "轮次类型或学期发生变化，请核验任务配置"
		return nil
	}
	officialMillis, err := strconv.ParseInt(academic.Text(contextData["official_time"]), 10, 64)
	if err != nil || officialMillis <= 0 {
		return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校时钟暂不可核验"}
	}
	officialNow := time.UnixMilli(officialMillis)
	ended := batch["state"] == "ended"
	if end, err := academic.ParseJWXKTaskTime(task.End); err == nil && !officialNow.Before(end) {
		ended = true
	}
	task.Execution["round_active"], task.Execution["round_ended"] = batch["can_enter"] == true && !ended, ended
	if begin, err := academic.ParseJWXKTaskTime(academic.Text(batch["begin_time"])); err == nil {
		task.Execution["round_overdue"] = !ended && officialNow.Sub(begin) >= 3*time.Minute
	}
	if len(task.ImportedOperations) > 0 {
		return api.verifyImportedTaskOperation(ctx, runtime, session, &task, mode)
	}
	if task.Type == "vacancy_swap" {
		return api.tickJWXKSwapStep(ctx, runtime, session, &task, mode, batch, officialNow, ended)
	}
	if task.Type == "weight_strategy" {
		return api.tickJWXKWeightStep(ctx, runtime, session, &task, mode, batch, officialNow, ended)
	}
	if task.PendingSelection != nil {
		if ended {
			may, err := selectionTaskMayReconcile(ctx, api, session, *task.PendingSelection)
			if err != nil {
				return err
			}
			if !may {
				task.PendingSelection = nil
				task.PendingGroup = ""
			} else {
				task.Status, task.Desired = "needs_review", "paused"
			}
		}
		if task.PendingSelection != nil {
			result, err := api.selectJWXKCourse(ctx, runtime, session, mode, *task.PendingSelection)
			if err != nil {
				return err
			}
			applySelectionTaskResult(&task, result)
			if ended {
				if task.PendingSelection != nil {
					task.Status, task.Desired, task.Message = "needs_review", "paused", "轮次已结束，仍有提交结果待核验"
					return nil
				}
				task.Desired, task.Status = "running", "running"
			} else {
				return nil
			}
		}
	}
	official, err := academic.FetchJWXKSelected(ctx, runtime, mode, task.Batch)
	if err != nil {
		return err
	}
	if official["selection_type_code"] != "02" || official["term_code"] != task.Term {
		return &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "本轮官方结果类型或学期变化"}
	}
	for _, feed := range official["unavailable_feeds"].([]string) {
		if feed == "selected" {
			return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "已选来源尚未完整读取"}
		}
	}
	selected := map[string]bool{}
	for _, course := range jwxkArchiveRows(official["selected"]) {
		if course["current_batch_record"] == true {
			selected[strings.ToUpper(academic.Text(course["course_code"]))] = true
		}
	}
	if task.GroupResults == nil {
		task.GroupResults = map[string]any{}
	}
	allDone := len(task.Groups) > 0
	for _, group := range task.Groups {
		codes := map[string]bool{}
		for _, item := range task.Items {
			if academic.Text(item["plan_group_id"]) == group.ID && selected[strings.ToUpper(academic.Text(item["course_code"]))] {
				codes[strings.ToUpper(academic.Text(item["course_code"]))] = true
			}
		}
		state := academic.Object(task.GroupResults[group.ID])
		if state == nil {
			state = map[string]any{}
		}
		state["group_id"], state["name"], state["target_count"], state["selected_count"] = group.ID, group.Name, group.Target, len(codes)
		state["success_count"] = len(codes)
		confirmedCodes := []string{}
		for code := range codes {
			confirmedCodes = append(confirmedCodes, code)
		}
		sort.Strings(confirmedCodes)
		state["confirmed_course_codes"] = confirmedCodes
		state["status"] = "waiting"
		if len(codes) >= group.Target {
			state["status"] = "success"
			state["candidate_status"] = "complete"
		} else {
			allDone = false
		}
		task.GroupResults[group.ID] = state
	}
	if allDone {
		task.Status, task.Desired, task.Message = "success", "paused", "全部方案组已由官方结果确认达标"
		return nil
	}
	if ended {
		task.Status, task.Desired, task.Message = "failed", "paused", "轮次已结束，尚未满足全部方案组目标"
		return nil
	}
	start, startError := academic.ParseJWXKTaskTime(task.Start)
	if batch["can_enter"] != true || startError == nil && officialNow.Before(start) {
		task.Status, task.Message = "waiting", "等待官方开放时间与账号资格核验"
		delay := 15 * time.Second
		if officialStart, err := academic.ParseJWXKTaskTime(academic.Text(batch["begin_time"])); err == nil && officialStart.After(start) {
			start = officialStart
		}
		if remaining := start.Sub(officialNow); remaining > 0 && remaining < delay {
			delay = max(time.Second, remaining)
		}
		task.NextAttempt = time.Now().UTC().Add(delay).Format(time.RFC3339Nano)
		return nil
	}
	if len(task.Groups) == 0 || len(task.Items) == 0 {
		task.Status, task.Desired, task.Message = "paused", "paused", "方案没有可执行候选，任务已暂停"
		return nil
	}
	if task.RejectedClasses == nil {
		task.RejectedClasses = map[string]string{}
	}
	for offset := range task.Groups {
		index := (task.NextGroupIndex + offset) % len(task.Groups)
		group := task.Groups[index]
		if academic.Object(task.GroupResults[group.ID])["status"] == "success" {
			continue
		}
		candidates := []map[string]any{}
		for _, item := range task.Items {
			if academic.Text(item["plan_group_id"]) == group.ID && !selected[strings.ToUpper(academic.Text(item["course_code"]))] {
				candidates = append(candidates, item)
			}
		}
		sort.SliceStable(candidates, func(left, right int) bool {
			first, second := candidates[left], candidates[right]
			if academic.Number(first["utility"]) != academic.Number(second["utility"]) {
				return academic.Number(first["utility"]) > academic.Number(second["utility"])
			}
			if academic.Text(first["course_code"]) != academic.Text(second["course_code"]) {
				return academic.Text(first["course_code"]) < academic.Text(second["course_code"])
			}
			return academic.Text(first["class_id"]) < academic.Text(second["class_id"])
		})
		for _, candidate := range candidates {
			class := academic.Text(candidate["class_id"])
			if task.RejectedClasses[class] != "" {
				continue
			}
			task.NextGroupIndex = (index + 1) % len(task.Groups)
			state := academic.Object(task.GroupResults[group.ID])
			state["current_class_id"], state["current_course_code"], state["current_course_name"], state["current_utility"] = class, candidate["course_code"], candidate["course_name"], candidate["utility"]
			state["candidate_status"], state["fallback_reason"] = "data_unknown", ""
			input := academic.JWXKSelect{Batch: task.Batch, Class: class, Course: academic.Text(candidate["course_code"]), Scope: academic.Text(candidate["teaching_class_type"]), ConfirmRisk: true}
			market, err := academic.SearchJWXKCourses(ctx, runtime, mode, academic.JWXKSearch{Batch: task.Batch, Scope: input.Scope, Keyword: input.Course, Page: 1, Size: 50})
			if err != nil {
				return err
			}
			var live map[string]any
			for _, course := range jwxkArchiveRows(market["courses"]) {
				if academic.Text(course["class_id"]) == class && strings.EqualFold(academic.Text(course["course_code"]), input.Course) {
					live = course
					break
				}
			}
			if live == nil {
				return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "当前候选暂未查询到，保持当前候选等待核验"}
			}
			state["current_candidate"], state["capacity"], state["selected_count_snapshot"] = candidate, live["capacity"], live["selected_count"]
			state["current_class_id"], state["current_course_code"], state["current_course_name"], state["current_utility"] = class, input.Course, candidate["course_name"], candidate["utility"]
			state["last_participant_snapshot"], state["data_updated_at"] = map[string]any{"capacity": live["capacity"], "selected_count": live["selected_count"]}, time.Now().UTC().Format(time.RFC3339Nano)
			if live["full"] == true {
				task.PollingMode = "vacancy_watch"
				task.NextAttempt = time.Now().UTC().Add(task.PollInterval()).Format(time.RFC3339Nano)
				task.RejectedClasses[class] = "FULL"
				task.Message = "官方确认当前候选满员，下次检查尝试后续候选"
				state["candidate_status"], state["fallback_reason"], state["message"] = "full", task.Message, task.Message
				return nil
			}
			task.PendingSelection, task.PendingGroup = &input, group.ID
			state["candidate_status"], state["pending_since"] = "submitting", time.Now().UTC().Format(time.RFC3339Nano)
			task.Execution["stage"], task.Execution["stage_code"] = "核验并提交当前候选", "selection_submit"
			if err := api.saveAutomationTask(ctx, session, &task); err != nil {
				return err
			}
			result, err := api.selectJWXKCourse(ctx, runtime, session, mode, input)
			if err != nil {
				return err
			}
			applySelectionTaskResult(&task, result)
			return nil
		}
	}
	task.RejectedClasses = map[string]string{}
	task.Status, task.Message = "waiting", "当前候选均未成功，等待下一轮空位检查"
	return nil
}

func applySelectionTaskResult(task *academic.JWXKAutomationTask, result academic.JWXKMutationResult) {
	if task.PendingSelection == nil {
		return
	}
	input := *task.PendingSelection
	if task.RejectedClasses == nil {
		task.RejectedClasses = map[string]string{}
	}
	encoded, _ := json.Marshal(result)
	var row map[string]any
	json.Unmarshal(encoded, &row)
	row["class_id"], row["course_code"], row["group_id"], row["at"] = input.Class, input.Course, task.PendingGroup, time.Now().UTC().Format(time.RFC3339Nano)
	task.Results = append(task.Results, row)
	if len(task.Results) > 80 {
		task.Results = task.Results[len(task.Results)-80:]
	}
	task.Status, task.Message = "waiting", result.Message
	state := academic.Object(task.GroupResults[task.PendingGroup])
	if state != nil {
		state["message"] = result.Message
		state["candidate_status"] = strings.ToLower(result.Failure)
		if result.State == "unknown" || result.State == "pending" {
			state["status"], state["candidate_status"] = "verifying", "mutation_uncertain"
		}
		if result.Success {
			state["candidate_status"] = "complete"
			delete(state, "pending_since")
		}
	}
	if result.Success {
		task.PendingSelection = nil
		task.PendingGroup = ""
		return
	}
	task.PollingMode = "vacancy_watch"
	task.NextAttempt = time.Now().UTC().Add(task.PollInterval()).Format(time.RFC3339Nano)
	if result.Failure == "AUTH_REQUIRED" || result.Failure == "RATE_LIMITED" {
		task.NextAttempt = time.Now().UTC().Add(time.Minute).Format(time.RFC3339Nano)
		task.Execution["last_error_type"], task.Execution["last_error_message"] = result.Failure, result.Message
	}
	if result.State == "rejected" || result.State == "not_sent" {
		if result.Failure == "FULL" || result.Failure == "UNAVAILABLE" || result.Failure == "RACE_LOST" {
			task.RejectedClasses[input.Class] = result.Failure
		}
		task.PendingSelection = nil
		task.PendingGroup = ""
	}
}
