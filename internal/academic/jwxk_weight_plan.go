package academic

import (
	"context"
	"sort"
	"strings"
)

type JWXKWeightPlan struct {
	JWXKSavedPlan
	GradeSize int `json:"grade_size"`
}

func (input JWXKWeightPlan) Valid() bool {
	return input.JWXKSavedPlan.Valid() && input.GradeSize >= 1 && input.GradeSize <= 100000 && len(input.Items) > 0 && len(input.Groups) > 0
}

func BuildJWXKWeightPlan(ctx context.Context, input JWXKWeightPlan, marketRows []map[string]any, official, budget map[string]any) (map[string]any, error) {
	if !input.Valid() || official["complete"] != true || Text(official["selection_type_code"]) != "04" || Text(official["term_code"]) != input.Term {
		return nil, weightError("权重策略需要完整的当前学期官方结果及有效方案")
	}
	market, marketByID := []WeightMarketCourse{}, map[string]map[string]any{}
	for _, course := range marketRows {
		if !JWXKBusinessCourse(course) {
			continue
		}
		capacity, capacityOK := jwxkWeightNumber(course["capacity"])
		bidders, biddersOK := jwxkWeightNumber(course["weight_participant_count"])
		id := Text(course["class_id"])
		if id == "" || !capacityOK || !biddersOK {
			return nil, weightError("当前市场缺少已确认的容量或已投注人数，请重新刷新")
		}
		marketByID[id] = course
		if capacity > 0 {
			market = append(market, WeightMarketCourse{id, capacity, bidders})
		}
	}
	byCode, order := map[string][]map[string]any{}, []string{}
	for _, item := range input.Items {
		code := strings.ToUpper(strings.TrimSpace(Text(item["course_code"])))
		live := marketByID[Text(item["class_id"])]
		if code == "" || live == nil || !strings.EqualFold(code, Text(live["course_code"])) {
			return nil, weightError("候选教学班不在本轮最新市场中，请刷新方案")
		}
		if byCode[code] == nil {
			order = append(order, code)
		}
		copy := map[string]any{}
		for field, value := range live {
			copy[field] = value
		}
		copy["utility"], copy["plan_group_id"] = item["utility"], item["plan_group_id"]
		if item["utility"] == nil {
			copy["utility"] = 5
		}
		byCode[code] = append(byCode[code], copy)
	}
	volunteered, confirmed := map[string]map[string]any{}, map[string]bool{}
	for _, course := range jwxkArchiveMeetings(official["volunteered"]) {
		if course["current_batch_record"] != true {
			continue
		}
		code := strings.ToUpper(Text(course["course_code"]))
		if volunteered[code] != nil {
			return nil, weightError("官方同课程志愿重复，无法可靠计算可回收权重")
		}
		volunteered[code] = course
	}
	for _, course := range jwxkArchiveMeetings(official["selected"]) {
		code := strings.ToUpper(Text(course["course_code"]))
		term := Text(course["record_term_code"])
		if volunteered[code] == nil && (term == "" || term == input.Term) {
			confirmed[code] = true
		}
	}
	remaining, remainingOK := jwxkWeightNumber(budget["remaining"])
	minimum, minimumOK := jwxkWeightNumber(budget["minimum"])
	step, stepOK := jwxkWeightNumber(budget["step"])
	if !remainingOK || !minimumOK || !stepOK {
		return nil, weightError("无法核验官方权重预算")
	}
	candidates, targets, meetings := []WeightCandidate{}, map[string]map[string]any{}, map[string][]SelectionMeeting{}
	reclaimable := 0
	for _, code := range order {
		alternatives := byCode[code]
		sort.SliceStable(alternatives, func(left, right int) bool {
			if Number(alternatives[left]["utility"]) != Number(alternatives[right]["utility"]) {
				return Number(alternatives[left]["utility"]) > Number(alternatives[right]["utility"])
			}
			return Text(alternatives[left]["class_id"]) < Text(alternatives[right]["class_id"])
		})
		target := alternatives[0]
		id := Text(target["class_id"])
		memberships, capacity, bidders, unknown := []string{}, 0, 0, false
		for _, item := range alternatives {
			memberships = append(memberships, Text(item["plan_group_id"]))
			capacity = max(capacity, int(Number(item["capacity"])))
			bidders = max(bidders, int(Number(item["weight_participant_count"])))
			arrangements := jwxkArchiveMeetings(item["schedules"])
			unknown = unknown || len(arrangements) == 0
			for _, row := range arrangements {
				meeting := NormalizeSelectionMeeting(row, input.Term, "jwxk_weight_plan")
				unknown = unknown || row["recurrence_unknown"] == true || len(meeting.Weeks) == 0 || meeting.Day < 1 || meeting.Day > 7 || !selectionTimingKnown(meeting)
			}
		}
		for _, row := range jwxkArchiveMeetings(target["schedules"]) {
			meeting := NormalizeSelectionMeeting(row, input.Term, "jwxk_weight_plan")
			meeting.SourceID, meeting.Code, meeting.Name = id, code, Text(target["course_name"])
			meetings[id] = append(meetings[id], meeting)
		}
		memberships = uniqueJWXK(memberships)
		sort.Strings(memberships)
		candidate := WeightCandidate{ID: id, Name: Text(target["course_name"]), Capacity: capacity, Bidders: bidders, Utility: Number(target["utility"]), Groups: memberships, AlreadySelected: confirmed[code], TimeUnknown: unknown}
		if current := volunteered[code]; current != nil {
			weight, valid := jwxkWeightNumber(current["devoted_weight"])
			if !valid {
				return nil, weightError("已有志愿权重未知，不能估算可回收预算")
			}
			reclaimable += weight
			target["current_weight"], target["current_class_id"], target["current_selection_source"] = weight, current["class_id"], current["selection_source"]
			target["reapply_required"] = true
		} else {
			target["current_weight"], target["current_class_id"], target["current_selection_source"], target["reapply_required"] = nil, "", "", false
		}
		target["current_participant_count"], target["current_capacity"], target["current_participant_label"] = bidders, capacity, "已投注人数"
		candidates, targets[id] = append(candidates, candidate), target
	}
	conflicts := [][2]string{}
	for left, first := range candidates {
		for _, second := range candidates[left+1:] {
			conflict := false
			for _, firstMeeting := range meetings[first.ID] {
				for _, secondMeeting := range meetings[second.ID] {
					checked := compareSelectionMeetings(firstMeeting, secondMeeting, "jwxk_weight_plan")
					conflict = conflict || checked["status"] == "conflict"
				}
			}
			if conflict {
				conflicts = append(conflicts, [2]string{first.ID, second.ID})
			}
		}
	}
	optimized, err := OptimizeGroupedWeights(ctx, WeightPolicy{Budget: remaining + reclaimable, Minimum: minimum, Step: step}, input.GradeSize, market, candidates, input.Groups, conflicts)
	if err != nil {
		return nil, err
	}
	courses, allocations := []map[string]any{}, []map[string]any{}
	for _, result := range optimized["courses"].([]map[string]any) {
		target := targets[Text(result["course_id"])]
		for field, value := range result {
			target[field] = value
		}
		target["weight"] = result["bid"]
		target["withdraw_only"] = Number(result["bid"]) == 0 && Number(target["current_weight"]) > 0 && result["already_selected"] != true
		courses = append(courses, target)
		if (Number(result["bid"]) > 0 || target["withdraw_only"] == true) && result["already_selected"] != true {
			allocations = append(allocations, target)
		}
	}
	optimized["courses"], optimized["items"], optimized["used"] = courses, allocations, optimized["budget_used"]
	optimized["budget"], optimized["official_remaining"], optimized["reclaimable_weight"] = remaining+reclaimable, remaining, reclaimable
	optimized["official_total"], optimized["official_used"], optimized["minimum"], optimized["step"] = budget["total"], budget["used"], minimum, step
	return optimized, nil
}
