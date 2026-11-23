package academic

import (
	"context"
	"sort"
	"strconv"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func JWXKFilterOptions(courses []map[string]any, menus []map[string]string, currentCampus, currentName string) map[string]any {
	result := map[string]any{"scopes": menus, "availability": []map[string]string{{"value": "selectable", "label": "本轮可选"}, {"value": "available", "label": "仍有余量"}, {"value": "conflict_free", "label": "官方未标冲突"}, {"value": "selected", "label": "已经选择"}}}
	weekdays, sections := []map[string]string{}, []map[string]string{}
	for index, label := range []rune("一二三四五六日") {
		weekdays = append(weekdays, map[string]string{"value": strconv.Itoa(index + 1), "label": "周" + string(label)})
	}
	for section := 1; section <= 30; section++ {
		value := strconv.Itoa(section)
		sections = append(sections, map[string]string{"value": value, "label": "第" + value + "节"})
	}
	result["weekdays"], result["sections"] = weekdays, sections
	for output, field := range map[string]string{"course_natures": "course_nature", "course_categories": "course_category", "general_elective_categories": "general_elective_category", "departments": "department"} {
		values := []string{}
		for _, course := range courses {
			values = append(values, Text(course[field]))
			if field == "course_category" {
				values = append(values, jwxkStrings(course["course_categories"])...)
			}
		}
		values = uniqueJWXK(values)
		sort.Strings(values)
		options := []map[string]string{}
		for _, value := range values {
			options = append(options, map[string]string{"value": value, "label": value})
		}
		result[output] = options
	}
	campuses := map[string]string{}
	addCampus := func(code, name string) {
		code = jwxkCampus(code)
		if code != "" {
			campuses[code] = firstText(jwxkCampusName(code, name), campuses[code], code)
		}
	}
	addCampus(currentCampus, currentName)
	for _, course := range courses {
		addCampus(Text(course["campus"]), Text(course["campus_name"]))
		meetings, _ := course["schedules"].([]map[string]any)
		if generic, ok := course["schedules"].([]any); ok {
			for _, item := range generic {
				if meeting := Object(item); meeting != nil {
					meetings = append(meetings, meeting)
				}
			}
		}
		for _, meeting := range meetings {
			addCampus(Text(meeting["campus"]), Text(meeting["campus_name"]))
		}
	}
	options := []map[string]string{}
	for code, name := range campuses {
		options = append(options, map[string]string{"value": code, "label": name})
	}
	sort.Slice(options, func(left, right int) bool {
		if options[left]["label"] == options[right]["label"] {
			return options[left]["value"] < options[right]["value"]
		}
		return options[left]["label"] < options[right]["label"]
	})
	result["campuses"] = options
	return result
}

func FetchJWXKFilterOptions(ctx context.Context, requester ServiceRequester, mode, batchCode string) (map[string]any, error) {
	if !jwxkBatchCode.MatchString(batchCode) {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "轮次参数无效"}
	}
	contextData, err := FetchJWXKContext(ctx, requester, mode)
	if err != nil {
		return nil, err
	}
	var batch map[string]any
	for _, candidate := range contextData["batches"].([]map[string]any) {
		if Text(candidate["code"]) == batchCode {
			batch = candidate
			break
		}
	}
	if batch == nil {
		return nil, &campus.Error{Code: "JWXK_BATCH_MISSING", Message: "选课轮次不存在或当前账号不可见"}
	}
	menus := batch["menus"].([]map[string]string)
	if len(menus) == 0 {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "尚未取得本轮课程范围，请刷新轮次后重试"}
	}
	scope := menus[0]["code"]
	for _, menu := range menus {
		if menu["code"] != "ALLKC" {
			scope = menu["code"]
			break
		}
	}
	page, err := SearchJWXKCourses(ctx, requester, mode, JWXKSearch{Batch: batchCode, Scope: scope, Page: 1, Size: 50})
	if err != nil {
		return nil, err
	}
	options := append([]map[string]string{{"code": "ALL", "name": "所有课程"}, {"code": "ROUND", "name": "本轮课程"}}, menus...)
	return JWXKFilterOptions(page["courses"].([]map[string]any), options, Text(contextData["current_campus"]), Text(contextData["current_campus_name"])), nil
}
