package academic

import (
	"sort"
	"strconv"
	"strings"
)

func jwxkArchiveMeetings(value any) []map[string]any {
	if rows, ok := value.([]map[string]any); ok {
		return rows
	}
	rows, _ := reportRows(value)
	return rows
}

func JWXKBusinessCourse(course map[string]any) bool {
	for _, scope := range append(jwxkStrings(course["source_scopes"]), Text(course["teaching_class_type"])) {
		if scope != "" && scope != "ALL" && scope != "ROUND" && scope != "ALLKC" {
			return true
		}
	}
	return false
}

func MergeJWXKArchive(previous, incoming []map[string]any) []map[string]any {
	result := []map[string]any{}
	byClass := map[string]map[string]any{}
	for _, course := range append(append([]map[string]any{}, previous...), incoming...) {
		key := Text(course["class_id"])
		if key == "" {
			continue
		}
		target := byClass[key]
		if target == nil {
			target = map[string]any{}
			byClass[key] = target
			result = append(result, target)
		}
		for field, value := range course {
			switch field {
			case "capacity", "total_capacity", "selected_count", "first_choice_count", "weight_participant_count", "market_participant_count", "devoted_weight", "selected", "course_already_selected", "full", "restricted", "conflict", "conflict_description":
				target[field] = value
			case "eligibility_status", "eligibility_reason":
				if course["eligibility_status"] != "unknown" || target["eligibility_status"] == nil || target["eligibility_status"] == "unknown" {
					target[field] = value
				}
			case "source_scopes", "source_tags", "course_categories":
				target[field] = uniqueJWXK(append(jwxkStrings(target[field]), jwxkStrings(value)...))
			case "teaching_class_type":
				if Text(value) != "ALLKC" || Text(target[field]) == "" {
					target[field] = value
				}
			default:
				if value != nil && Text(value) != "" && Text(value) != "[]" {
					target[field] = value
				}
			}
		}
	}
	return result
}

func QueryJWXKArchive(courses []map[string]any, input JWXKCatalogSearch) map[string]any {
	filtered := []map[string]any{}
	for _, course := range courses {
		if !matchesJWXKArchive(course, input) {
			continue
		}
		filtered = append(filtered, course)
	}
	groups := GroupJWXKCourses(filtered)
	sort.SliceStable(groups, func(left, right int) bool {
		first, second := groups[left], groups[right]
		if Text(first["course_name"]) != Text(second["course_name"]) {
			return Text(first["course_name"]) < Text(second["course_name"])
		}
		return Text(first["course_code"]) < Text(second["course_code"])
	})
	start := min((input.Page-1)*input.Size, len(groups))
	return map[string]any{"total": len(groups), "groups": groups[start:min(start+input.Size, len(groups))], "scope": input.Scope, "cache_hit": true, "data_source": "local", "sync_status": "pending", "catalog_complete": false}
}

func matchesJWXKArchive(course map[string]any, input JWXKCatalogSearch) bool {
	if input.Scope == "ALL" || input.Scope == "ROUND" {
		if !JWXKBusinessCourse(course) {
			return false
		}
	} else {
		matched := false
		for _, scope := range append(jwxkStrings(course["source_scopes"]), Text(course["teaching_class_type"])) {
			matched = matched || scope == input.Scope
		}
		if !matched {
			return false
		}
	}
	keyword := strings.ToLower(strings.TrimSpace(input.Keyword))
	if keyword != "" && !strings.Contains(strings.ToLower(Text(course["course_code"])+" "+Text(course["course_name"])+" "+Text(course["teacher"])), keyword) {
		return false
	}
	if !matchesJWXKCatalog(course, input) {
		return false
	}
	for key, field := range map[string]string{"KCXZ": "course_nature", "KKDW": "department", "XGXKLB": "general_elective_category", "XGXKLBDM": "general_elective_category_code"} {
		if value := input.Filters[key]; value != "" && value != Text(course[field]) {
			return false
		}
	}
	if input.Filters["SFCT"] == "0" && course["conflict"] == true || input.Filters["SFYM"] == "0" && course["full"] == true || input.Filters["SFYX"] == "1" && course["selected"] != true {
		return false
	}
	weekday, _ := strconv.Atoi(input.Filters["SKXQ"])
	start, _ := strconv.Atoi(input.Filters["KSJC"])
	end, _ := strconv.Atoi(input.Filters["JSJC"])
	if weekday > 0 || start > 0 || end > 0 {
		meetings := jwxkArchiveMeetings(course["schedules"])
		matched := false
		for _, meeting := range meetings {
			if (start > 0 || end > 0) && (Number(meeting["start_section"]) <= 0 || Number(meeting["end_section"]) <= 0) {
				continue
			}
			if weekday > 0 && int(Number(meeting["weekday"])) != weekday {
				continue
			}
			if start > 0 && Number(meeting["end_section"]) < float64(start) || end > 0 && Number(meeting["start_section"]) > float64(end) {
				continue
			}
			matched = true
		}
		if !matched {
			return false
		}
	}
	return true
}
