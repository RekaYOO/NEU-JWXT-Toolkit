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

func swapTaskState(task *academic.JWXKAutomationTask, group string) map[string]any {
	if task.SwapResults == nil {
		task.SwapResults = map[string]any{}
	}
	state := academic.Object(task.SwapResults[group])
	if state == nil {
		state = map[string]any{"group_id": group, "status": "monitoring"}
		task.SwapResults[group] = state
	}
	if academic.Object(state["drop_states"]) == nil {
		state["drop_states"] = map[string]any{}
	}
	return state
}

func applySwapTaskResult(task *academic.JWXKAutomationTask, group, class, action string, result academic.JWXKMutationResult) {
	state := swapTaskState(task, group)
	state["message"], task.Message = result.Message, result.Message
	encoded, _ := json.Marshal(result)
	row := map[string]any{}
	json.Unmarshal(encoded, &row)
	row["group_id"], row["class_id"], row["action"], row["at"] = group, class, action, time.Now().UTC().Format(time.RFC3339Nano)
	task.Results = append(task.Results, row)
	if len(task.Results) > 80 {
		task.Results = task.Results[len(task.Results)-80:]
	}
	if result.Success {
		state["status"], state["inflight_mutation"] = "monitoring", nil
		if action == "drop" {
			academic.Object(state["drop_states"])[class] = "confirmed"
			task.PendingDrop, task.PendingDropGroup = nil, ""
		} else {
			state["status"] = "success"
			task.PendingSelection, task.PendingGroup = nil, ""
		}
		task.Status = "waiting"
		return
	}
	if result.State == "not_sent" || result.State == "rejected" {
		state["inflight_mutation"] = nil
		if action == "drop" {
			task.PendingDrop, task.PendingDropGroup = nil, ""
		} else {
			task.PendingSelection, task.PendingGroup = nil, ""
		}
	}
	state["status"] = "needs_review"
	task.Status, task.Desired = "needs_review", "paused"
}

func (api *API) tickJWXKSwapStep(ctx context.Context, runtime *Runtime, session store.Session, task *academic.JWXKAutomationTask, mode string, batch map[string]any, officialNow time.Time, ended bool) error {
	if task.PendingDrop != nil {
		input, group := *task.PendingDrop, task.PendingDropGroup
		attempt, err := api.database.SelectionAttempt(ctx, session, jwxkDeselectionKey(input.Batch, input.Class))
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return err
		}
		if err == nil && (attempt.State == "pending" || attempt.State == "unknown" || attempt.State == "confirmed") {
			result, err := api.deselectJWXKCourse(ctx, runtime, session, mode, input)
			if err != nil {
				return err
			}
			applySwapTaskResult(task, group, input.Class, "drop", result)
			return nil
		}
		task.PendingDrop, task.PendingDropGroup = nil, ""
		swapTaskState(task, group)["inflight_mutation"] = nil
	}
	if task.PendingSelection != nil {
		input, group := *task.PendingSelection, task.PendingGroup
		may, err := selectionTaskMayReconcile(ctx, api, session, input)
		if err != nil {
			return err
		}
		if may {
			result, err := api.selectJWXKCourse(ctx, runtime, session, mode, input)
			if err != nil {
				return err
			}
			applySwapTaskResult(task, group, input.Class, "select", result)
			return nil
		}
		task.PendingSelection, task.PendingGroup = nil, ""
		swapTaskState(task, group)["inflight_mutation"] = nil
	}
	selected, err := academic.FetchJWXKSelected(ctx, runtime, mode, task.Batch)
	if err != nil {
		return err
	}
	if selected["selection_type_code"] != "02" || selected["term_code"] != task.Term {
		return &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "换课轮次或学期发生变化"}
	}
	for _, feed := range selected["unavailable_feeds"].([]string) {
		if feed == "selected" {
			return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "已选来源不完整，停止换课步骤"}
		}
	}
	classes := map[string]map[string]any{}
	for _, course := range jwxkArchiveRows(selected["selected"]) {
		if course["current_batch_record"] == true {
			classes[academic.Text(course["class_id"])] = course
		}
	}
	allDone := len(task.SwapGroups) > 0
	for _, group := range task.SwapGroups {
		state := swapTaskState(task, group.ID)
		if row := classes[group.Target.Class]; row != nil && strings.EqualFold(academic.Text(row["course_code"]), group.Target.Course) {
			state["status"], state["message"] = "success", "目标教学班已由官方结果确认"
		} else {
			state["status"] = "monitoring"
			allDone = false
		}
	}
	if allDone {
		task.Status, task.Desired, task.Message = "success", "paused", "全部换课目标已由官方结果确认"
		return nil
	}
	if ended {
		task.Status, task.Desired, task.Message = "failed", "paused", "轮次已结束，换课目标尚未全部完成"
		return nil
	}
	start, startError := academic.ParseJWXKTaskTime(task.Start)
	if batch["can_enter"] != true || startError == nil && officialNow.Before(start) {
		task.Status, task.Message = "waiting", "等待官方开放时间与账号资格核验"
		return nil
	}
	for offset := range task.SwapGroups {
		index := (task.NextGroupIndex + offset) % len(task.SwapGroups)
		group := task.SwapGroups[index]
		state := swapTaskState(task, group.ID)
		if state["status"] == "success" {
			continue
		}
		task.NextGroupIndex = (index + 1) % len(task.SwapGroups)
		market, err := academic.SearchJWXKCourses(ctx, runtime, mode, academic.JWXKSearch{Batch: task.Batch, Scope: group.Target.Scope, Keyword: group.Target.Course, Page: 1, Size: 50})
		if err != nil {
			return err
		}
		var target map[string]any
		for _, row := range jwxkArchiveRows(market["courses"]) {
			if academic.Text(row["class_id"]) == group.Target.Class && strings.EqualFold(academic.Text(row["course_code"]), group.Target.Course) {
				target = row
				break
			}
		}
		if target == nil || target["capacity"] == nil || target["selected_count"] == nil {
			return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "目标空位数据不完整，不会退选已有课程"}
		}
		state["last_participant_snapshot"] = map[string]any{"capacity": target["capacity"], "selected_count": target["selected_count"]}
		state["data_updated_at"] = time.Now().UTC().Format(time.RFC3339Nano)
		if target["full"] == true || academic.Number(target["capacity"]) <= academic.Number(target["selected_count"]) {
			state["message"], task.Message, task.Status = "目标仍然满员，保留已有课程", "目标仍然满员，保留已有课程", "waiting"
			return nil
		}
		dropStates := academic.Object(state["drop_states"])
		for _, drop := range group.Drop {
			if classes[drop.Class] == nil || dropStates[drop.Class] == "confirmed" {
				dropStates[drop.Class] = "confirmed"
				continue
			}
			if !strings.EqualFold(academic.Text(classes[drop.Class]["course_code"]), drop.Course) {
				return &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: "待退课程标识发生变化，停止换课"}
			}
			input := academic.JWXKDeselect{Batch: task.Batch, Class: drop.Class, Source: "yxkcyx"}
			task.PendingDrop, task.PendingDropGroup = &input, group.ID
			state["inflight_mutation"] = map[string]any{"action": "drop", "class_id": drop.Class, "group_id": group.ID}
			state["status"], dropStates[drop.Class] = "verifying_drop", "submitting"
			task.Execution["stage"], task.Execution["stage_code"] = "核验并退选指定课程", "swap_drop"
			if err := api.saveAutomationTask(ctx, session, task); err != nil {
				return err
			}
			result, err := api.deselectJWXKCourse(ctx, runtime, session, mode, input)
			if err != nil {
				return err
			}
			applySwapTaskResult(task, group.ID, drop.Class, "drop", result)
			return nil
		}
		input := academic.JWXKSelect{Batch: task.Batch, Class: group.Target.Class, Course: group.Target.Course, Scope: group.Target.Scope}
		task.PendingSelection, task.PendingGroup = &input, group.ID
		state["inflight_mutation"] = map[string]any{"action": "select", "class_id": input.Class, "group_id": group.ID}
		state["status"] = "verifying"
		task.Execution["stage"], task.Execution["stage_code"] = "核验并提交换课目标", "swap_select"
		if err := api.saveAutomationTask(ctx, session, task); err != nil {
			return err
		}
		result, err := api.selectJWXKCourse(ctx, runtime, session, mode, input)
		if err != nil {
			return err
		}
		applySwapTaskResult(task, group.ID, input.Class, "select", result)
		return nil
	}
	return nil
}
