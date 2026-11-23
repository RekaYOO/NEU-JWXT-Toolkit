package academic

import (
	"strings"
	"unicode/utf8"
)

type JWXKPlanGroup struct {
	ID     string `json:"group_id"`
	Name   string `json:"name"`
	Target int    `json:"target_count"`
}

type JWXKSavedPlan struct {
	Batch       string              `json:"batch_code"`
	Term        string              `json:"term_code"`
	Groups      []JWXKPlanGroup     `json:"groups"`
	Items       []map[string]any    `json:"items"`
	Experiments map[string][]string `json:"experiment_selections"`
	Revision    int64               `json:"revision"`
}

func (plan JWXKSavedPlan) Valid() bool {
	if !jwxkBatchCode.MatchString(plan.Batch) || !timetableTermPattern.MatchString(plan.Term) || plan.Revision < 0 || len(plan.Groups) > 20 || len(plan.Items) > 100 || len(plan.Experiments) > 100 {
		return false
	}
	seen := map[string]bool{}
	for _, group := range plan.Groups {
		if !jwxkBatchCode.MatchString(group.ID) || seen[group.ID] || strings.TrimSpace(group.Name) == "" || utf8.RuneCountInString(group.Name) > 60 || group.Target < 1 || group.Target > 20 {
			return false
		}
		seen[group.ID] = true
	}
	for _, item := range plan.Items {
		if item == nil {
			return false
		}
	}
	for class, schedules := range plan.Experiments {
		if !jwxkBatchCode.MatchString(class) || len(schedules) > 30 {
			return false
		}
		for _, schedule := range schedules {
			if !jwxkBatchCode.MatchString(schedule) {
				return false
			}
		}
	}
	return true
}

func NormalizeJWXKPlan(plan JWXKSavedPlan) JWXKSavedPlan {
	if plan.Groups == nil {
		plan.Groups = []JWXKPlanGroup{}
	}
	if plan.Experiments == nil {
		plan.Experiments = map[string][]string{}
	}
	for class, schedules := range plan.Experiments {
		plan.Experiments[class] = uniqueJWXK(schedules)
	}
	items := []map[string]any{}
	allowed := strings.Fields("course_code course_name class_id class_number credits hours teacher location official_schedule campus campus_name department course_nature course_category course_categories normalized_course_category general_elective_category_code general_elective_category exam_type_code exam_type score_scale_code score_scale teaching_class_type teaching_mode has_experiment experiment_hours teacher_titles target_classes total_capacity capacity selected_count first_choice_count weight_participant_count devoted_weight selection_source record_batch_code record_term_code conflict conflict_description restricted eligibility_status eligibility_reason full selected course_already_selected has_test has_book notice selection_type_code market_participant_count market_participant_label market_capacity_label source_tags campuses source_scopes group_id plan_group_id plan_group_name plan_group_target_count utility weight enabled")
	meetingFields := strings.Fields("meeting_id term_code week_text week_mask weeks recurrence_unknown parse_status weekday start_section end_section start_time end_time teacher campus campus_name location raw_text schedule_source schedule_id project_name group_id group_name target_classes hours")
	for _, raw := range plan.Items {
		item := map[string]any{}
		for _, field := range allowed {
			if value, exists := raw[field]; exists {
				item[field] = value
			}
		}
		utility := int(Number(raw["utility"]))
		if utility < 1 || utility > 10 {
			priority := int(Number(raw["priority"]))
			utility = 5
			if priority != 0 {
				utility = max(1, min(10, 11-priority))
			}
		}
		item["utility"] = utility
		group := firstText(raw["plan_group_id"], raw["group_id"], raw["course_code"], raw["class_id"])
		if group != "" {
			item["plan_group_id"] = group
			item["plan_group_name"] = firstText(raw["plan_group_name"], raw["course_name"], "方案组")
			count := int(Number(raw["plan_group_target_count"]))
			if count < 1 {
				count = 1
			}
			item["plan_group_target_count"] = min(count, 20)
		}
		for _, field := range []string{"schedules", "experiment_schedules"} {
			rows := jwxkArchiveMeetings(raw[field])
			meetings := []map[string]any{}
			for _, row := range rows {
				meeting := map[string]any{}
				for _, key := range meetingFields {
					if value, exists := row[key]; exists {
						meeting[key] = value
					}
				}
				if len(ParseWeeks(meeting["weeks"], false)) == 0 {
					mask := Text(meeting["week_mask"])
					if jwxkWeekMask.MatchString(mask) {
						weeks := []any{}
						for position, enabled := range mask {
							if enabled == '1' {
								weeks = append(weeks, position+1)
							}
						}
						if len(weeks) > 0 {
							meeting["weeks"] = weeks
						}
					}
				}
				normalized := NormalizeSelectionMeeting(meeting, plan.Term, "jwxk_plan")
				meeting["meeting_id"], meeting["weeks"], meeting["recurrence_unknown"] = normalized.ID, normalized.Weeks, len(normalized.Weeks) == 0
				if normalized.Weeks == nil {
					meeting["weeks"] = []int{}
				}
				meetings = append(meetings, meeting)
			}
			item[field] = meetings
		}
		items = append(items, item)
	}
	plan.Items = items
	return plan
}
