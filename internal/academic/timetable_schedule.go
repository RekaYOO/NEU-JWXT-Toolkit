package academic

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type TimetableSchedule struct {
	TimetableContext
	Week *int `json:"week"`
}

func (query TimetableSchedule) Valid() bool {
	return query.TimetableContext.Valid() && query.Campus != "" && (query.Week == nil || *query.Week >= 1 && *query.Week <= 30)
}

func detailTexts(value any) []string {
	result := []string{}
	if rows, ok := value.([]any); ok {
		for _, raw := range rows {
			if object := Object(raw); object != nil {
				raw = object["text"]
			}
			if text := strings.TrimSpace(Text(raw)); text != "" {
				result = append(result, text)
			}
		}
	}
	return result
}

var namesSeparator = regexp.MustCompile(`[,，、;/]`)
var teacherBracket = regexp.MustCompile(`\[[^\]]+\]`)
var safeCourseColor = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
var gradingPattern = regexp.MustCompile(`(?:考试|考查)\s*[/／·|]\s*([^\s/／·|]+)`)
var timetableNaturePattern = regexp.MustCompile(`(?:^|[\s/／·|，,；;：:（(])((?:专业|公共|通识|限选|任选)?(?:必修|选修))(?:$|[\s/／·|，,；;）)])`)
var campusLocationPattern = regexp.MustCompile(`([^\s，,]*校区)\s+(.+)$`)
var roomLocationPattern = regexp.MustCompile(`(?:楼|馆|教室|实验室|实验中心|体育场|建筑|信息|\d+号)[^\s，,]*$`)

func listNames(value any) []string {
	result := []string{}
	if rows, ok := value.([]any); ok {
		for _, raw := range rows {
			if object := Object(raw); object != nil {
				raw = object["name"]
			}
			if text := strings.TrimSpace(Text(raw)); text != "" {
				result = append(result, text)
			}
		}
	} else {
		for _, part := range namesSeparator.Split(Text(value), -1) {
			if text := strings.TrimSpace(part); text != "" {
				result = append(result, text)
			}
		}
	}
	return result
}
func firstValue(row map[string]any, keys ...string) any {
	for _, key := range keys {
		value := row[key]
		if value == nil || Text(value) == "" {
			continue
		}
		if list, ok := value.([]any); ok && len(list) == 0 {
			continue
		}
		return value
	}
	return nil
}

func timetableCourse(row map[string]any, query TimetableSchedule) map[string]any {
	cell, title, tags := detailTexts(row["cellDetail"]), detailTexts(row["titleDetail"]), detailTexts(row["tags"])
	fallback := "未命名课程"
	if len(cell) > 0 {
		fallback = cell[0]
	}
	name, code, class := firstText(row["courseName"], row["KCM"], row["name"], fallback), firstText(row["courseCode"], row["courseNo"], row["KCH"]), firstText(row["teachClassId"], row["teachingClassId"], row["JXBID"])
	digest := sha256.Sum256([]byte(strings.Join([]string{name, code, class, Text(row["dayOfWeek"]), Text(row["beginSection"]), Text(row["endSection"]), strings.Join(title, "\x1e")}, "\x1f")))
	identifier := firstText(class, hex.EncodeToString(digest[:])[:20])
	lines := append(append(append([]string{}, title...), cell...), tags...)
	text := strings.Join(lines, " ")
	nature := firstText(row["courseNatureName"], row["KCXZDM_DISPLAY"], row["KCXZ_DISPLAY"])
	if nature == "" {
		for _, value := range []any{row["courseNature"], row["KCXZ"]} {
			label := strings.TrimSpace(Text(value))
			if strings.Contains(label, "必修") || strings.Contains(label, "选修") {
				nature = label
				break
			}
		}
	}
	if nature == "" {
		for _, line := range lines {
			if line == name {
				continue
			}
			if match := timetableNaturePattern.FindStringSubmatch(line); len(match) > 1 {
				nature = match[1]
				break
			}
		}
	}
	assessment, grading := "", ""
	for _, label := range []string{"考试", "考查"} {
		if strings.Contains(text, label) {
			assessment = label
			break
		}
	}
	if match := gradingPattern.FindStringSubmatch(text); len(match) > 1 {
		grading = match[1]
	}
	location, campusName := firstText(row["location"], row["place"], row["roomName"], row["classroom"], row["JASMC"]), firstText(row["campus"], row["campusName"], row["XQMC"])
	if location == "" {
		for _, line := range title {
			if match := campusLocationPattern.FindStringSubmatch(line); len(match) > 2 {
				campusName = firstText(campusName, match[1])
				location = match[1] + " " + match[2]
				break
			}
			tokens := strings.Fields(line)
			for index := len(tokens) - 1; index >= 0; index-- {
				token := strings.Trim(tokens[index], "，,。；;")
				if roomLocationPattern.MatchString(token) {
					location = token
					break
				}
			}
			if location != "" {
				break
			}
		}
	}
	teachers := listNames(firstValue(row, "teachers", "teacherName"))
	if len(teachers) == 0 {
		if rows, ok := row["weeksAndTeachers"].([]any); ok {
			for _, raw := range rows {
				if object := Object(raw); object != nil {
					raw = firstValue(object, "teacherName", "teachers", "name")
				}
				teachers = append(teachers, listNames(raw)...)
			}
		} else {
			parts := strings.Split(Text(row["weeksAndTeachers"]), "/")
			for _, part := range parts {
				if !strings.Contains(part, "周") && !strings.Contains(strings.ToLower(part), "week") {
					teachers = append(teachers, listNames(teacherBracket.ReplaceAllString(part, ""))...)
				}
			}
		}
	}
	unique, seen := []string{}, map[string]bool{}
	for _, teacher := range teachers {
		if !seen[teacher] {
			unique = append(unique, teacher)
			seen[teacher] = true
		}
	}
	teachers = unique
	weekSource := firstValue(row, "weeks", "weekList", "week_list", "weekText")
	strict := false
	if weekSource == nil {
		weekSource = row["weeksAndTeachers"]
		strict = true
	}
	if weekSource == nil || Text(weekSource) == "" {
		weekSource = strings.Join(append(append([]string{}, title...), cell...), " ")
		strict = true
	}
	weeks := ParseWeeks(weekSource, strict)
	if len(weeks) == 0 && query.Week != nil {
		weeks = []int{*query.Week}
	}
	color := Text(row["color"])
	if !safeCourseColor.MatchString(color) {
		color = "#2563eb"
	}
	activity, label := "other", "课程"
	tagText := strings.ToLower(strings.Join(tags, " "))
	for _, spec := range []struct {
		kind, label string
		words       []string
	}{{"lecture", "理论课", []string{"理论", "讲授", "lecture"}}, {"experiment", "实验课", []string{"实验", "experiment", "lab"}}, {"practice", "实践课", []string{"实践", "实习", "课程设计", "practice"}}, {"physical", "体育课", []string{"体育", "physical"}}} {
		found := false
		for _, word := range spec.words {
			if strings.Contains(tagText, word) {
				found = true
			}
		}
		if found {
			activity, label = spec.kind, spec.label
			break
		}
	}
	result := map[string]any{"id": identifier, "course_name": name, "course_code": code, "teaching_class_id": class, "weekday": int(Number(firstValue(row, "dayOfWeek", "SKXQ"))), "start_section": int(Number(firstValue(row, "beginSection", "KSJC"))), "end_section": int(Number(firstValue(row, "endSection", "JSJC"))), "start_time": firstText(row["beginTime"], row["KSSJ"]), "end_time": firstText(row["endTime"], row["JSSJ"]), "teachers": teachers, "classes": listNames(firstValue(row, "classes", "className", "classNames")), "location": location, "campus": campusName, "course_nature": nature, "assessment_type": assessment, "grading_scheme": grading, "cell_details": cell, "title_details": title, "tags": tags, "preselected": strings.Contains(strings.Join(tags, " ")+" "+firstText(row["selectionStatus"], row["courseStatus"], row["XKZT_DISPLAY"], row["ZT_DISPLAY"]), "预选"), "color": color, "activity_type": activity, "activity_type_label": label, "weeks": weeks, "recurrence_unknown": len(weeks) == 0}
	stable := map[string]any{"source": query.Mode + "_timetable", "source_id": identifier, "term_code": query.Term, "course_name": name, "course_code": code, "teaching_class_id": class, "weeks": weeks, "weekday": result["weekday"], "start_section": result["start_section"], "end_section": result["end_section"], "start_time": result["start_time"], "end_time": result["end_time"], "location": location, "campus": campusName}
	encoded, _ := json.Marshal(stable)
	meeting := sha256.Sum256(encoded)
	result["meeting_id"] = "mtg_" + hex.EncodeToString(meeting[:])[:24]
	return result
}

func FetchTimetableSchedule(ctx context.Context, requester Requester, query TimetableSchedule) (map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "课表查询条件无效"}
	}
	campusCode := query.Campus
	if campusCode == "all" {
		campusCode = ""
	}
	form := url.Values{"XNXQDM": {query.Term}, "XQDM": {campusCode}}
	path := "kbapp/api/wdkbcx/getMyScheduleDetail.do"
	if query.Week != nil {
		form.Set("ZC", strconv.Itoa(*query.Week))
	}
	if query.Mode != "personal" {
		path = "kbapp/api/qxkbcx/getScheduleDetail.do"
		form.Set("CODE", query.Target)
		form.Set("KBLX", timetableModes[query.Mode])
	}
	body, err := timetablePost(ctx, requester, path, form)
	if err != nil {
		return nil, err
	}
	data := Object(body)
	if nested := Object(data["datas"]); nested != nil {
		data = nested
	}
	hasList := func(node map[string]any) bool {
		for _, key := range []string{"arrangedList", "notArrangeList", "practiceList"} {
			if _, ok := node[key]; ok {
				return true
			}
		}
		return false
	}
	if !hasList(data) {
		keys := []string{}
		for key := range data {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if nested := Object(data[key]); hasList(nested) {
				data = nested
				break
			}
		}
	}
	if !hasList(data) {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表响应缺少课程列表"}
	}
	result := map[string]any{"mode": query.Mode, "term_code": query.Term, "campus_code": query.Campus, "target_id": query.Target, "week": query.Week}
	for _, spec := range [][2]string{{"arrangedList", "courses"}, {"notArrangeList", "unscheduled"}, {"practiceList", "practices"}} {
		value, exists := data[spec[0]]
		if exists && value == nil {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表课程列表无效"}
		}
		rows, err := reportRows(value)
		if err != nil {
			return nil, err
		}
		courses := []map[string]any{}
		for _, row := range rows {
			if spec[1] == "courses" {
				courses = append(courses, timetableCourse(row, query))
			} else {
				details := detailTexts(row["titleDetail"])
				fallback := "未命名课程"
				if spec[1] == "practices" {
					fallback = "未命名实践"
				}
				if len(details) > 0 {
					fallback = details[0]
				}
				courses = append(courses, map[string]any{"course_name": firstText(row["courseName"], row["KCM"], row["name"], fallback), "course_code": firstText(row["courseCode"], row["KCH"]), "details": details})
			}
		}
		result[spec[1]] = courses
	}
	return result, nil
}
