package academic

import (
	"sort"
	"strings"
)

func jwxkStrings(value any) []string {
	if values, ok := value.([]string); ok {
		return values
	}
	result := []string{}
	if values, ok := value.([]any); ok {
		for _, item := range values {
			result = append(result, Text(item))
		}
	}
	return result
}

func GroupJWXKCourses(courses []map[string]any) []map[string]any {
	groups := []map[string]any{}
	byID := map[string]map[string]any{}
	fields := []string{"course_code", "course_name", "credits", "hours", "department", "course_nature", "course_category", "normalized_course_category", "general_elective_category_code", "general_elective_category", "exam_type_code", "exam_type", "score_scale_code", "score_scale"}
	for _, course := range courses {
		identity := strings.ToLower(Text(course["course_code"]))
		if identity == "" {
			identity = strings.ToLower(Text(course["course_name"])) + "|" + Text(course["credits"]) + "|" + strings.ToLower(Text(course["department"]))
		}
		id := jwxkID("jwxk-course-", identity)
		group := byID[id]
		if group == nil {
			group = map[string]any{"group_id": id, "classes": []map[string]any{}, "course_categories": []string{}, "campuses": []string{}, "source_tags": []string{}}
			byID[id] = group
			groups = append(groups, group)
		}
		for _, field := range fields {
			if Text(group[field]) == "" {
				group[field] = Text(course[field])
			}
		}
		group["course_categories"] = uniqueJWXK(append(append(jwxkStrings(group["course_categories"]), jwxkStrings(course["course_categories"])...), Text(course["course_category"])))
		for _, category := range jwxkStrings(group["course_categories"]) {
			if jwxkCategory(category) == "通识选修" {
				group["normalized_course_category"] = "通识选修"
			}
		}
		group["source_tags"] = uniqueJWXK(append(jwxkStrings(group["source_tags"]), jwxkStrings(course["source_tags"])...))
		campuses := append(jwxkStrings(group["campuses"]), jwxkCampusName(Text(course["campus"]), Text(course["campus_name"])))
		for _, meeting := range jwxkArchiveMeetings(course["schedules"]) {
			campuses = append(campuses, jwxkCampusName(Text(meeting["campus"]), Text(meeting["campus_name"])))
		}
		group["campuses"] = uniqueJWXK(campuses)
		group["classes"] = append(group["classes"].([]map[string]any), course)
	}
	for _, group := range groups {
		if Text(group["course_name"]) == "" {
			group["course_name"] = "未命名课程"
		}
		classes := group["classes"].([]map[string]any)
		participants := func(course map[string]any) any {
			if course["market_participant_count"] != nil {
				return course["market_participant_count"]
			}
			return course["selected_count"]
		}
		sort.SliceStable(classes, func(left, right int) bool {
			first, second := classes[left], classes[right]
			blockedFirst, blockedSecond := first["restricted"] == true || first["full"] == true, second["restricted"] == true || second["full"] == true
			if blockedFirst != blockedSecond {
				return !blockedFirst
			}
			if (first["conflict"] == true) != (second["conflict"] == true) {
				return first["conflict"] != true
			}
			firstRemaining, secondRemaining := max(0, Number(first["capacity"])-Number(participants(first))), max(0, Number(second["capacity"])-Number(participants(second)))
			if firstRemaining != secondRemaining {
				return firstRemaining > secondRemaining
			}
			return Text(first["teacher"]) < Text(second["teacher"])
		})
		selectable, pending, available, conflictFree := 0, 0, 0, 0
		for _, course := range classes {
			if course["eligibility_status"] == "selectable" && course["restricted"] != true && course["full"] != true {
				selectable++
			}
			if course["eligibility_status"] == "unknown" {
				pending++
			}
			if course["capacity"] != nil && participants(course) != nil && Number(course["capacity"]) > Number(participants(course)) {
				available++
			}
			if course["conflict"] != true {
				conflictFree++
			}
		}
		group["class_count"], group["selectable_count"], group["eligibility_pending_count"], group["available_count"], group["conflict_free_count"] = len(classes), selectable, pending, available, conflictFree
	}
	return groups
}
