package application

import (
	"encoding/json"
	"errors"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func legacyTasks(root *os.Root, account string) ([]legacyCandidate, error) {
	var rows []json.RawMessage
	found, err := readLegacy(root, "course_selection_tasks.json", &rows)
	if err != nil || !found {
		return nil, err
	}
	result := []legacyCandidate{}
	for _, raw := range rows {
		var owner struct {
			Account string `json:"account"`
		}
		if json.Unmarshal(raw, &owner) != nil {
			return nil, errors.New("legacy task owner is invalid")
		}
		if owner.Account != account {
			continue
		}
		task, operations, err := convertLegacyTask(raw)
		if err != nil {
			return nil, err
		}
		payload, err := json.Marshal(task)
		if err != nil {
			return nil, err
		}
		result = append(result, legacyCandidate{"course_selection_tasks.json", jwxkAutomationTaskNamespace, task.ID, payload})
		for _, operation := range operations {
			key, err := importedOperationKey(operation)
			if err != nil {
				return nil, err
			}
			class := ""
			if operation.Selection != nil {
				class = operation.Selection.Class
			} else {
				class = operation.Removal.Class
			}
			payload, _ := json.Marshal(store.SelectionAttempt{State: "unknown", Number: 1, Course: operation.Course, Term: task.Term, Batch: task.Batch, Class: class})
			result = append(result, legacyCandidate{"course_selection_tasks.json", "selection-attempts", key, payload})
		}
	}
	return result, nil
}

func convertLegacyTask(raw json.RawMessage) (academic.JWXKAutomationTask, []academic.JWXKImportedOperation, error) {
	request := academic.DefaultJWXKAutomationRequest()
	if json.Unmarshal(raw, &request) != nil {
		return academic.JWXKAutomationTask{}, nil, errors.New("legacy task parameters are invalid")
	}
	var previous map[string]any
	if json.Unmarshal(raw, &previous) != nil {
		return academic.JWXKAutomationTask{}, nil, errors.New("legacy task state is invalid")
	}
	created := time.Unix(0, 0).UTC()
	if value := academic.Text(previous["created_at"]); value != "" {
		parsed, err := academic.ParseJWXKTaskTime(value)
		if err != nil {
			return academic.JWXKAutomationTask{}, nil, errors.New("legacy task creation time is invalid")
		}
		created = parsed
	}
	request.Revision = 0
	task, err := academic.NewJWXKAutomationTask(request, academic.Text(previous["task_id"]), created)
	if err != nil {
		return task, nil, err
	}
	task.Status, task.Desired, task.RestartReconcile = "paused", "paused", true
	task.Message = "旧版任务已导入并暂停；启动后先核验学校结果"
	switch academic.Text(previous["status"]) {
	case "success", "failed", "cancelled":
		task.Status = academic.Text(previous["status"])
	}
	if task.Status == "cancelled" {
		task.Desired = "cancelled"
	}
	if value := academic.Text(previous["updated_at"]); value != "" {
		task.Updated = value
	}
	task.Attempts = max(0, int(academic.Number(previous["attempt_count"])))
	task.LastAttempt = academic.Text(previous["last_attempt_at"])
	for _, row := range jwxkArchiveRows(previous["results"]) {
		clean := map[string]any{}
		for _, field := range strings.Fields("group_id action class_id course_code course_name code message at success state") {
			if value, exists := row[field]; exists {
				clean[field] = value
			}
		}
		task.Results = append(task.Results, clean)
	}
	lookup := map[string]map[string]any{}
	for _, row := range request.Items {
		lookup[academic.Text(row["class_id"])] = row
	}
	for _, group := range request.SwapGroups {
		for _, course := range append(append([]academic.JWXKAutomationCourse{}, group.Drop...), group.Target) {
			lookup[course.Class] = map[string]any{"class_id": course.Class, "course_code": course.Course, "teaching_class_type": course.Scope}
		}
	}
	weight := academic.Object(previous["weight_status"])
	for _, field := range []string{"recommendation", "pending_add", "pending_drop"} {
		for _, row := range jwxkArchiveRows(weight[field]) {
			class := academic.Text(row["class_id"])
			merged := map[string]any{}
			for key, value := range lookup[class] {
				merged[key] = value
			}
			for key, value := range row {
				merged[key] = value
			}
			lookup[class] = merged
		}
	}
	operations := []academic.JWXKImportedOperation{}
	seen := map[string]string{}
	add := func(action, class string, details map[string]any) error {
		candidate := map[string]any{}
		for key, value := range lookup[class] {
			candidate[key] = value
		}
		for key, value := range details {
			if value != nil && academic.Text(value) != "" {
				candidate[key] = value
			}
		}
		course := academic.Text(candidate["course_code"])
		if class == "" || course == "" {
			return errors.New("legacy pending operation cannot be matched to a course")
		}
		operation := academic.JWXKImportedOperation{Course: course}
		switch action {
		case "select", "add", "weight_add":
			input := academic.JWXKSelect{Batch: task.Batch, Class: class, Course: course, Scope: academic.Text(candidate["teaching_class_type"])}
			if task.Type == "weight_strategy" {
				weight := int(academic.Number(candidate["weight"]))
				if weight < 1 {
					return errors.New("legacy pending weight is missing")
				}
				input.Weight = &weight
			}
			operation.Action, operation.Selection = "select", &input
		case "drop", "weight_drop":
			operation.Action, operation.Removal = "drop", &academic.JWXKDeselect{Batch: task.Batch, Class: class}
		default:
			return errors.New("legacy pending operation action is unsupported")
		}
		key, err := importedOperationKey(operation)
		if err != nil {
			return err
		}
		encoded, _ := json.Marshal(operation)
		if previous, exists := seen[key]; exists {
			if previous != string(encoded) {
				return errors.New("legacy pending operations conflict for the same course")
			}
		} else {
			operations = append(operations, operation)
			seen[key] = string(encoded)
		}
		return nil
	}
	if inflight := academic.Object(previous["inflight_mutation"]); inflight != nil {
		if err := add(academic.Text(inflight["action"]), academic.Text(inflight["class_id"]), inflight); err != nil {
			return task, nil, err
		}
	}
	groupResults := academic.Object(previous["group_results"])
	groupIDs := []string{}
	for key := range groupResults {
		groupIDs = append(groupIDs, key)
	}
	sort.Strings(groupIDs)
	for _, id := range groupIDs {
		state := academic.Object(groupResults[id])
		classes, _ := state["pending_class_ids"].([]any)
		for _, class := range classes {
			if err := add("select", academic.Text(class), nil); err != nil {
				return task, nil, err
			}
		}
		status := academic.Text(state["candidate_status"])
		if len(classes) == 0 && (status == "submitting" || status == "verifying" || academic.Text(state["status"]) == "needs_review" || academic.Text(state["status"]) == "verifying") {
			if err := add("select", academic.Text(state["current_class_id"]), nil); err != nil {
				return task, nil, err
			}
		}
	}
	swapResults := academic.Object(previous["swap_results"])
	for _, group := range request.SwapGroups {
		state := academic.Object(swapResults[group.ID])
		classes, _ := state["pending_drop_ids"].([]any)
		for _, class := range classes {
			if err := add("drop", academic.Text(class), nil); err != nil {
				return task, nil, err
			}
		}
		if class := academic.Text(state["current_drop_id"]); class != "" {
			if err := add("drop", class, nil); err != nil {
				return task, nil, err
			}
		}
		for class, status := range academic.Object(state["drop_states"]) {
			if status == "submitting" || status == "verifying" || status == "unknown" {
				if err := add("drop", class, nil); err != nil {
					return task, nil, err
				}
			}
		}
		if state["target_pending"] == true {
			if err := add("select", group.Target.Class, nil); err != nil {
				return task, nil, err
			}
		}
	}
	if inflight := academic.Object(weight["inflight"]); inflight != nil {
		if err := add(academic.Text(inflight["action"]), academic.Text(inflight["class_id"]), inflight); err != nil {
			return task, nil, err
		}
	}
	sort.Slice(operations, func(left, right int) bool {
		first, _ := importedOperationKey(operations[left])
		second, _ := importedOperationKey(operations[right])
		return operations[left].Action+first < operations[right].Action+second
	})
	task.ImportedOperations = operations
	if len(operations) > 0 && task.Status != "cancelled" {
		task.Status, task.Message = "needs_review", "旧版任务含待确认提交，启动后仅核验；确认前不会继续执行"
	}
	return task, operations, nil
}
