package academic

import (
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"time"
	"unicode/utf8"
)

type JWXKAutomationCourse struct {
	Class   string `json:"class_id"`
	Course  string `json:"course_code"`
	Name    string `json:"course_name"`
	Scope   string `json:"teaching_class_type"`
	Teacher string `json:"teacher"`
}

func (course JWXKAutomationCourse) Valid(batch string) bool {
	return (JWXKSelect{Batch: batch, Class: course.Class, Course: course.Course, Scope: course.Scope}).Valid() && utf8.RuneCountInString(course.Name) <= 120 && utf8.RuneCountInString(course.Teacher) <= 120
}

type JWXKSwapGroup struct {
	ID     string                 `json:"group_id"`
	Name   string                 `json:"name"`
	Target JWXKAutomationCourse   `json:"target"`
	Drop   []JWXKAutomationCourse `json:"drop_courses"`
}

type JWXKAutomationRequest struct {
	JWXKSavedPlan
	Name             string          `json:"name"`
	Type             string          `json:"task_type"`
	SwapGroups       []JWXKSwapGroup `json:"swap_groups"`
	GradeSize        *int            `json:"grade_size"`
	RebalanceSeconds int             `json:"rebalance_seconds"`
	ReplaceExisting  bool            `json:"replace_existing"`
	Start            string          `json:"start_at"`
	End              string          `json:"end_at"`
	PollSeconds      int             `json:"poll_seconds"`
}

func DefaultJWXKAutomationRequest() JWXKAutomationRequest {
	return JWXKAutomationRequest{Name: "自动抢课任务", Type: "selection", RebalanceSeconds: 1800, PollSeconds: 15}
}

func ParseJWXKTaskTime(value string) (time.Time, error) {
	if parsed, err := time.Parse(time.RFC3339, value); err == nil {
		return parsed, nil
	}
	zone := time.FixedZone("Asia/Shanghai", 8*3600)
	for _, layout := range []string{"2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02T15:04"} {
		if parsed, err := time.ParseInLocation(layout, value, zone); err == nil {
			return parsed, nil
		}
	}
	return time.Time{}, errors.New("invalid task time")
}

func (request JWXKAutomationRequest) Valid() bool {
	if !request.JWXKSavedPlan.Valid() || strings.TrimSpace(request.Name) == "" || utf8.RuneCountInString(request.Name) > 80 || request.PollSeconds < 1 || request.PollSeconds > 300 || request.RebalanceSeconds < 600 || request.RebalanceSeconds > 86400 || len(request.SwapGroups) > 20 {
		return false
	}
	if request.Type != "selection" && request.Type != "vacancy_swap" && request.Type != "weight_strategy" {
		return false
	}
	if request.Type == "weight_strategy" && request.GradeSize == nil {
		return false
	}
	if request.GradeSize != nil && (*request.GradeSize < 1 || *request.GradeSize > 100000) {
		return false
	}
	var start, end time.Time
	var err error
	if request.Start != "" {
		start, err = ParseJWXKTaskTime(request.Start)
		if err != nil {
			return false
		}
	}
	if request.End != "" {
		end, err = ParseJWXKTaskTime(request.End)
		if err != nil {
			return false
		}
	}
	if !start.IsZero() && !end.IsZero() && !end.After(start) {
		return false
	}
	groups := map[string]bool{}
	for _, group := range request.Groups {
		groups[group.ID] = true
	}
	for _, item := range request.Items {
		course := JWXKAutomationCourse{Class: Text(item["class_id"]), Course: Text(item["course_code"]), Scope: Text(item["teaching_class_type"]), Name: Text(item["course_name"]), Teacher: Text(item["teacher"])}
		if !course.Valid(request.Batch) {
			return false
		}
		if request.Type != "vacancy_swap" && !groups[firstText(item["plan_group_id"], item["group_id"], item["course_code"], item["class_id"])] {
			return false
		}
	}
	seen := map[string]bool{}
	for _, group := range request.SwapGroups {
		if !jwxkBatchCode.MatchString(group.ID) || seen[group.ID] || strings.TrimSpace(group.Name) == "" || utf8.RuneCountInString(group.Name) > 80 || !group.Target.Valid(request.Batch) || len(group.Drop) < 1 || len(group.Drop) > 20 {
			return false
		}
		seen[group.ID] = true
		drops := map[string]bool{}
		for _, course := range group.Drop {
			if !course.Valid(request.Batch) || drops[course.Class] || course.Class == group.Target.Class {
				return false
			}
			drops[course.Class] = true
		}
	}
	if request.Type == "vacancy_swap" {
		return len(request.SwapGroups) > 0
	}
	return len(request.Groups) > 0 && len(request.Items) > 0
}

type JWXKAutomationTask struct {
	JWXKAutomationRequest
	ID                 string                  `json:"task_id"`
	Status             string                  `json:"status"`
	Desired            string                  `json:"desired_state"`
	Message            string                  `json:"message"`
	Created            string                  `json:"created_at"`
	Updated            string                  `json:"updated_at"`
	LastAttempt        string                  `json:"last_attempt_at"`
	Attempts           int                     `json:"attempt_count"`
	BoundGroups        []string                `json:"bound_group_ids"`
	PollingMode        string                  `json:"polling_mode"`
	ManualCheck        string                  `json:"manual_check_requested_at,omitempty"`
	RestartReconcile   bool                    `json:"restart_reconcile"`
	Results            []map[string]any        `json:"results"`
	GroupResults       map[string]any          `json:"group_results"`
	SwapResults        map[string]any          `json:"swap_results"`
	WeightStatus       map[string]any          `json:"weight_status,omitempty"`
	Execution          map[string]any          `json:"execution"`
	NextAttempt        string                  `json:"next_attempt_at"`
	PendingSelection   *JWXKSelect             `json:"pending_selection,omitempty"`
	PendingGroup       string                  `json:"pending_group,omitempty"`
	PendingDrop        *JWXKDeselect           `json:"pending_drop,omitempty"`
	PendingDropGroup   string                  `json:"pending_drop_group,omitempty"`
	RejectedClasses    map[string]string       `json:"rejected_classes,omitempty"`
	NextGroupIndex     int                     `json:"next_group_index"`
	WeightApplication  *JWXKSavedPlan          `json:"weight_application,omitempty"`
	WeightScanJob      string                  `json:"weight_scan_job,omitempty"`
	ImportedOperations []JWXKImportedOperation `json:"imported_operations,omitempty"`
}

type JWXKImportedOperation struct {
	Action    string        `json:"action"`
	Course    string        `json:"course_code"`
	Selection *JWXKSelect   `json:"selection,omitempty"`
	Removal   *JWXKDeselect `json:"removal,omitempty"`
}

func (task JWXKAutomationTask) MarshalJSON() ([]byte, error) {
	type taskPayload JWXKAutomationTask
	return json.Marshal(struct {
		taskPayload
		PollIntervalSeconds int `json:"poll_interval_seconds"`
	}{taskPayload: taskPayload(task), PollIntervalSeconds: int(task.PollInterval() / time.Second)})
}

func NewJWXKAutomationTask(request JWXKAutomationRequest, id string, now time.Time) (JWXKAutomationTask, error) {
	if !request.Valid() || !jwxkBatchCode.MatchString(id) {
		return JWXKAutomationTask{}, errors.New("invalid automation task")
	}
	request.JWXKSavedPlan = NormalizeJWXKPlan(request.JWXKSavedPlan)
	request.ReplaceExisting = false
	task := JWXKAutomationTask{JWXKAutomationRequest: request, ID: id, Status: "draft", Desired: "paused", Message: "任务已保存，启动后才会访问选课系统", Created: now.UTC().Format(time.RFC3339Nano), Updated: now.UTC().Format(time.RFC3339Nano), BoundGroups: []string{}, Results: []map[string]any{}, GroupResults: map[string]any{}, SwapResults: map[string]any{}, Execution: map[string]any{"state": "idle", "stage": "尚未执行", "stage_code": "idle", "events": []any{}}}
	for _, group := range request.Groups {
		task.BoundGroups = append(task.BoundGroups, group.ID)
		task.GroupResults[group.ID] = map[string]any{"group_id": group.ID, "name": group.Name, "target_count": group.Target, "selected_count": 0, "status": "pending", "candidate_index": 0}
	}
	for _, group := range request.SwapGroups {
		task.SwapResults[group.ID] = map[string]any{"group_id": group.ID, "name": group.Name, "status": "pending", "pending_drop": group.Drop, "inflight_mutation": nil}
	}
	if request.Type == "weight_strategy" {
		task.WeightStatus = map[string]any{"phase": "idle", "recommendation": []any{}, "pending_drop": []any{}, "pending_add": []any{}, "inflight": nil, "confirmation_checks": 0}
	}
	task.PollingMode = task.pollingMode(now)
	return task, nil
}

func (task JWXKAutomationTask) pollingMode(now time.Time) string {
	if task.Type == "weight_strategy" {
		return "weight_rebalance"
	}
	if task.Type == "vacancy_swap" {
		return "vacancy_watch"
	}
	if start, err := ParseJWXKTaskTime(task.Start); err == nil && now.Before(start) {
		return "opening_burst"
	}
	return "vacancy_watch"
}

func (task *JWXKAutomationTask) Action(action string, now time.Time) error {
	terminal := task.Status == "success" || task.Status == "failed" || task.Status == "cancelled"
	switch action {
	case "start":
		if terminal {
			return errors.New("已结束任务不能重新启动")
		}
		task.Desired, task.Status, task.RestartReconcile = "running", "running", true
		if start, err := ParseJWXKTaskTime(task.Start); err == nil && now.Before(start) {
			task.Status = "waiting"
		}
		task.PollingMode = task.pollingMode(now)
		task.Message = "任务已启动，先核验当前官方状态与未完成操作"
	case "pause":
		if terminal {
			return errors.New("已结束任务不能暂停")
		}
		task.Desired, task.Status, task.Message = "paused", "paused", "任务已暂停"
	case "check_now":
		if task.Desired != "running" || task.Status != "running" && task.Status != "waiting" {
			return errors.New("请先启动任务，再执行立即检查")
		}
		task.ManualCheck = now.UTC().Format(time.RFC3339Nano)
		task.Message = "已请求立即检查，将在满足学校端最小请求间隔后执行"
	case "cancel":
		task.Desired, task.Status, task.Message = "cancelled", "cancelled", "任务已取消，保留未完成操作记录供核验"
	default:
		return errors.New("未知任务操作")
	}
	task.Updated = now.UTC().Format(time.RFC3339Nano)
	return nil
}

func (task JWXKAutomationTask) PollInterval() time.Duration {
	if task.Type == "weight_strategy" {
		if task.WeightApplication != nil || task.WeightScanJob != "" {
			return 15 * time.Second
		}
		return time.Duration(max(600, task.RebalanceSeconds)) * time.Second
	}
	if task.PollingMode == "opening_burst" {
		return time.Second
	}
	return time.Duration(max(15, task.PollSeconds)) * time.Second
}

func (task *JWXKAutomationTask) SyncPlan(plan JWXKSavedPlan, now time.Time) bool {
	if task.Batch != plan.Batch || task.Term != plan.Term || task.Type == "vacancy_swap" || task.Status == "cancelled" || task.Status == "success" || task.Status == "failed" {
		return false
	}
	bound := map[string]bool{}
	for _, group := range task.BoundGroups {
		bound[group] = true
	}
	if len(bound) == 0 {
		for _, group := range task.Groups {
			bound[group.ID] = true
			task.BoundGroups = append(task.BoundGroups, group.ID)
		}
	}
	groups, items := []JWXKPlanGroup{}, []map[string]any{}
	for _, group := range plan.Groups {
		if bound[group.ID] {
			groups = append(groups, group)
		}
	}
	for _, item := range plan.Items {
		if bound[Text(item["plan_group_id"])] {
			items = append(items, item)
		}
	}
	if reflect.DeepEqual(task.Groups, groups) && reflect.DeepEqual(task.Items, items) {
		return false
	}
	task.Groups, task.Items, task.Updated = groups, items, now.UTC().Format(time.RFC3339Nano)
	task.RejectedClasses = nil
	for _, group := range groups {
		state := Object(task.GroupResults[group.ID])
		if state == nil {
			state = map[string]any{"status": "pending", "selected_count": 0, "candidate_index": 0}
		}
		state["group_id"], state["name"], state["target_count"] = group.ID, group.Name, group.Target
		if task.GroupResults == nil {
			task.GroupResults = map[string]any{}
		}
		task.GroupResults[group.ID] = state
	}
	if len(groups) == 0 || len(items) == 0 {
		task.Desired, task.Status, task.Message = "paused", "paused", "绑定的方案组已删除或没有候选课程，任务已暂停"
	} else {
		task.Message = "方案组内容已同步，等待下一次检查"
	}
	if task.Type == "weight_strategy" {
		if task.WeightStatus == nil {
			task.WeightStatus = map[string]any{}
		}
		task.WeightStatus["recommendation"], task.WeightStatus["warnings"] = []any{}, []string{"方案组已变更，将在下一次策略检查时重新计算"}
	}
	if task.Execution == nil {
		task.Execution = map[string]any{}
	}
	events := jwxkArchiveMeetings(task.Execution["events"])
	events = append(events, map[string]any{"at": task.Updated, "level": "info", "stage_code": "plan_sync", "message": "绑定的方案组与候选课程已同步"})
	if len(events) > 80 {
		events = events[len(events)-80:]
	}
	task.Execution["events"] = events
	return true
}

func (task *JWXKAutomationTask) SyncTimes(startText, endText string, now time.Time) (bool, error) {
	start, startError := ParseJWXKTaskTime(startText)
	end, endError := ParseJWXKTaskTime(endText)
	if startError != nil || endError != nil || !end.After(start) {
		return false, errors.New("选课轮次时间无效")
	}
	if task.Status == "success" || task.Status == "cancelled" || task.Start == startText && task.End == endText {
		return false, nil
	}
	if task.End != endText && task.WeightStatus != nil {
		for _, field := range []string{"final_5_executed", "final_3_executed", "final_rebalance_executed", "final_notice_executed", "final_check_executed", "final_window_requested", "final_rebalance_requested"} {
			delete(task.WeightStatus, field)
		}
	}
	task.Start, task.End, task.Updated = startText, endText, now.UTC().Format(time.RFC3339Nano)
	task.PollingMode = task.pollingMode(now)
	task.Message = "轮次时间已同步，启动后按新时间执行"
	if task.Desired == "running" {
		switch {
		case now.Before(start):
			task.Status, task.Message = "waiting", "轮次时间已同步，等待新的开放时间"
		case now.After(end):
			task.Desired, task.Status, task.Message = "paused", "paused", "同步后的轮次已经结束，任务已暂停"
		case task.Status == "running" || task.Status == "waiting":
			task.Status, task.Message = "running", "轮次时间已同步，按新时间继续运行"
		}
	}
	return true, nil
}
