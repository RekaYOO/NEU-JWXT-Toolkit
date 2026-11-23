package academic

import (
	"regexp"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

var jwxkTeacherTitle = regexp.MustCompile(`[（(]([^()（）]+)[）)]$`)
var jwxkCategorySuffix = regexp.MustCompile(`课程$`)

func jwxkDisplay(code, display string, labels map[string]string) (string, string) {
	if display != "" && display != code {
		return code, display
	}
	return code, labels[code]
}
func jwxkCategory(value string) string {
	value = strings.Join(strings.Fields(value), "")
	switch value {
	case "通识选修类", "通识选修课", "通识选修课程", "通识选修":
		return "通识选修"
	}
	return jwxkCategorySuffix.ReplaceAllString(value, "")
}
func uniqueJWXK(values []string) []string {
	result := []string{}
	seen := map[string]bool{}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" && !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}

func normalizeJWXKClass(row, parent map[string]any) map[string]any {
	course := map[string]any{}
	for key, value := range parent {
		course[key] = value
	}
	for key, value := range row {
		course[key] = value
	}
	classID := firstText(course["JXBID"], course["KXH"])
	schedules := jwxkSchedules(row, course, classID)
	experiments := jwxkExperiments(course, classID)
	experimentHours := jwxkDecimal(course["SYXS"])
	teachingMode := Text(course["XSXLX"])
	locations := []string{}
	scheduleCampus, scheduleCampusName := "", ""
	for _, meeting := range schedules {
		locations = append(locations, Text(meeting["location"]))
		if scheduleCampus == "" {
			scheduleCampus = Text(meeting["campus"])
		}
		if scheduleCampusName == "" {
			scheduleCampusName = Text(meeting["campus_name"])
		}
	}
	capacity := jwxkInteger(course["KRL"])
	if capacity == nil {
		capacity = jwxkInteger(course["classCapacity"])
	}
	selected := jwxkInteger(course["YXRS"])
	if selected == nil {
		selected = jwxkInteger(course["numberOfSelected"])
	}
	examCode, exam := jwxkDisplay(firstText(course["KSLX"], course["examType"]), firstText(course["KSLXMC"], course["examTypeName"]), map[string]string{"01": "考试", "02": "考查"})
	scaleCode, scale := jwxkDisplay(Text(course["CJJLFS"]), Text(course["CJJLFSMC"]), map[string]string{"100": "百分制", "200": "等级制", "300": "两级制"})
	category := firstText(course["KCLB"], course["courseType"])
	categories := []string{}
	if category != "" {
		categories = append(categories, category)
	}
	campusCode := jwxkCampus(firstText(course["XQDM"], course["campusCode"], scheduleCampus, course["campus"], course["XQ"], course["teachCampus"]))
	campusName := jwxkCampusName(campusCode, firstText(course["XQMC"], course["campusName"], course["teachCampusName"], course["XQ"], scheduleCampusName))
	teachers := []map[string]string{}
	for _, raw := range strings.Split(Text(course["SKJSLB"]), ",") {
		if strings.TrimSpace(raw) == "" {
			continue
		}
		parts := strings.Split(raw, "|")
		name := strings.TrimSpace(parts[0])
		title := ""
		if match := jwxkTeacherTitle.FindStringSubmatch(name); len(match) > 0 {
			title = match[1]
			name = strings.TrimSpace(jwxkTeacherTitle.ReplaceAllString(name, ""))
		}
		id := ""
		if len(parts) > 1 {
			id = strings.TrimSpace(parts[1])
		}
		teachers = append(teachers, map[string]string{"name": name, "teacher_id": id, "title": title})
	}
	targets := []string{}
	for _, field := range []string{"TJBJ", "schoolClassMapStr"} {
		targets = append(targets, strings.Split(Text(course[field]), ",")...)
	}
	for _, field := range []string{"underLimitKind", "postLimitKind"} {
		if limit := Object(course[field]); limit != nil {
			rows, _ := reportRows(limit["teachingTargets"])
			for _, target := range rows {
				targets = append(targets, Text(target["className"]))
			}
		}
	}
	full := Text(course["SFYM"]) == "1" || capacity != nil && selected != nil && capacity.(int) > 0 && selected.(int) >= capacity.(int)
	return map[string]any{
		"course_code": Text(course["KCH"]), "course_name": Text(course["KCM"]), "class_id": classID, "class_number": Text(course["KXH"]), "credits": Text(course["XF"]), "hours": firstText(course["XS"], course["hours"]), "teacher": Text(course["SKJS"]), "location": strings.Join(uniqueJWXK(locations), "；"), "official_schedule": firstText(course["YPSJDD"], course["teachingPlace"]), "campus": campusCode, "campus_name": campusName,
		"department": firstText(course["KKDW"], course["CDDW"], course["departmentName"]), "course_nature": firstText(course["KCXZ"], course["courseNature"]), "course_category": category, "course_categories": categories, "normalized_course_category": jwxkCategory(category), "general_elective_category_code": firstText(course["XGXKLBDM"], course["generalElectiveCategoryCode"]), "general_elective_category": firstText(course["XGXKLB"], course["XGXKLBMC"], course["XGXKLBDM_DISPLAY"], course["generalElectiveCategoryName"]), "exam_type_code": examCode, "exam_type": exam, "score_scale_code": scaleCode, "score_scale": scale,
		"teaching_class_type": firstText(course["teachingClassType"], course["clazzType"]), "teaching_mode": teachingMode, "has_experiment": len(experiments) > 0 || Number(experimentHours) > 0 || strings.Contains(teachingMode, "实验"), "experiment_hours": experimentHours, "experiment_schedules": experiments, "teacher_details": teachers, "teacher_titles": Text(course["SKJSZC"]), "target_classes": uniqueJWXK(targets),
		"total_capacity": capacity, "capacity": capacity, "selected_count": selected, "first_choice_count": jwxkInteger(firstText(course["DYZYRS"], course["numberOfFirstVolunteer"])), "weight_participant_count": jwxkInteger(course["QZXKRS"]), "devoted_weight": jwxkInteger(course["TRQZ"]), "selection_source": Text(course["_selection_source"]), "official_operation_available": course["_official_operation_available"] == true, "record_batch_code": firstText(course["electiveBatchCode"], course["batchCode"]), "record_term_code": firstText(course["schoolTerm"], course["termCode"], course["semester"], course["xnxq"]),
		"conflict": Text(course["SFCT"]) == "1", "conflict_description": Text(course["conflictDesc"]), "restricted": Text(course["SFXZXK"]) == "1", "eligibility_status": "unknown", "eligibility_reason": "", "full": full, "selected": Text(course["SFYX"]) == "1", "course_already_selected": Text(parent["SFYX"]) == "1" || Text(course["SFYX"]) == "1", "has_test": Text(course["hasTest"]) == "1", "has_book": Text(course["hasBook"]) == "1", "notice": Text(course["XKSM"]), "schedules": schedules,
	}
}

func NormalizeJWXKCourses(value any) ([]map[string]any, error) {
	if _, ok := value.([]any); !ok {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校选课课程列表结构异常"}
	}
	rows, err := reportRows(value)
	if err != nil {
		return nil, err
	}
	result := []map[string]any{}
	for _, parent := range rows {
		if parent["tcList"] != nil {
			children, err := reportRows(parent["tcList"])
			if err != nil {
				return nil, err
			}
			if len(children) > 0 {
				for _, child := range children {
					result = append(result, normalizeJWXKClass(child, parent))
				}
				continue
			}
		}
		result = append(result, normalizeJWXKClass(parent, map[string]any{}))
	}
	return result, nil
}

func ApplyJWXKMarket(courses []map[string]any, mode string) {
	for _, course := range courses {
		capacity := course["total_capacity"]
		if capacity == nil {
			capacity = course["capacity"]
		}
		course["total_capacity"], course["capacity"] = capacity, capacity
		course["selection_type_code"] = mode
		course["market_participant_count"], course["market_participant_label"], course["market_capacity_label"] = course["selected_count"], "已选人数", "容量"
		if mode == "04" {
			if capacity != nil && course["selected_count"] != nil {
				remaining := Number(capacity) - Number(course["selected_count"])
				if remaining < 0 {
					remaining = 0
				}
				course["capacity"] = int(remaining)
			}
			course["market_participant_count"], course["market_participant_label"], course["market_capacity_label"], course["full"] = course["weight_participant_count"], "已投注人数", "可选容量", false
		}
	}
}
