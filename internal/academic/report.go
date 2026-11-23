package academic

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func firstText(values ...any) string {
	for _, value := range values {
		if text := Text(value); text != "" {
			return text
		}
	}
	return ""
}

func reportRows(value any) ([]map[string]any, error) {
	result := []map[string]any{}
	if value == nil {
		return result, nil
	}
	rows, ok := value.([]any)
	if !ok {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "培养计划列表结构异常"}
	}
	for _, raw := range rows {
		row := Object(raw)
		if row == nil {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "培养计划记录结构异常"}
		}
		result = append(result, row)
	}
	return result, nil
}

func reportCourse(row map[string]any, path []string, hint string) map[string]any {
	modern := row["courseName"] != nil || row["courseId"] != nil || row["credit"] != nil || row["passed"] != nil
	name, code, nature := Text(row["KCM"]), Text(row["KCH"]), Text(row["KCXZDM_DISPLAY"])
	credit, score, passed, status := Number(row["XF"]), Text(row["XSZCJ"]), Text(row["SFJG_DISPLAY"]) == "是", Text(row["ZT_DISPLAY"])
	selectTerm, scoreTerm, planTerm := Text(row["XKXNXQDM_DISPLAY"]), Text(row["CJXNXQDM_DISPLAY"]), Text(row["JHXNXQDM_DISPLAY"])
	retake := Text(row["CXCKDM_DISPLAY"])
	exam, substitute, substituteCredit, department := Text(row["KSLXDM_DISPLAY"]), Text(row["TDKCM"]), Number(row["TDKCXF"]), Text(row["KKDWDM_DISPLAY"])
	if modern {
		name, code, nature = Text(row["courseName"]), Text(row["courseId"]), firstText(row["courseNature"], hint)
		switch nature {
		case "01":
			nature = "必修"
		case "02":
			nature = "选修"
		}
		credit, score, passed = Number(row["credit"]), firstText(row["scoreView"], row["point"]), row["passed"] == true
		status = Text(row["status"])
		switch status {
		case "01":
			status = "通过"
		case "02":
			status = "挂科"
		case "03":
			status = "已选课"
		case "04":
			status = "未修读"
		}
		selectTerm, scoreTerm, planTerm = Text(row["courseSelectionSchoolYearTermCode"]), Text(row["pointSchoolYearTermCode"]), Text(row["schoolYearTerms"])
		retake = Text(row["retakeType"])
		switch retake {
		case "01":
			retake = "初修"
		case "02":
			retake = "重修"
		}
		exam = Text(row["examType"])
		switch exam {
		case "01":
			exam = "考试"
		case "02":
			exam = "考查"
		}
		substitute, substituteCredit, department = Text(row["replacedCourseName"]), Number(row["replacedCourseCredit"]), Text(row["courseDept"])
	}
	selected, display := !passed && status == "已选课", "未修读"
	if passed {
		display = "已通过"
	} else if selected {
		display = "已选课"
	}
	category := ""
	if len(path) > 0 {
		category = path[len(path)-1]
	}
	return map[string]any{"course_name": name, "course_code": code, "course_nature": nature, "credit": credit, "score": score,
		"is_passed": passed, "is_selected": selected, "is_planned": !passed && !selected, "status": status, "status_display": display,
		"term_code": firstText(scoreTerm, selectTerm, planTerm), "select_term_code": selectTerm, "exam_type": exam, "is_core": false,
		"score_term_code": scoreTerm, "plan_term_code": planTerm, "retake_status": retake,
		"substitute_course_name": substitute, "substitute_credit": substituteCredit, "dept_code": department, "category_path": path, "category_name": category}
}

type reportNode struct {
	payload     map[string]any
	rawPassed   float64
	rawSelected float64
	reported    *bool
}

func rounded(value float64) float64 { return math.Round(value*100) / 100 }

func reportCategory(row map[string]any, parent []string, hint string, depth int) (reportNode, error) {
	if depth > 64 {
		return reportNode{}, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "培养计划类别层级过深"}
	}
	name := firstText(row["name"], row["courseCategoryName"], "未命名")
	path := append(append([]string{}, parent...), name)
	courses, err := reportRows(row["checkCourseVOS"])
	if err != nil {
		return reportNode{}, err
	}
	legacy, err := reportRows(row["data"])
	if err != nil {
		return reportNode{}, err
	}
	requiredNature, electiveNature := false, false
	for _, course := range append(append([]map[string]any{}, courses...), legacy...) {
		nature := firstText(course["courseNature"], course["KCXZDM"])
		requiredNature = requiredNature || nature == "01"
		electiveNature = electiveNature || nature == "02"
	}
	requirement := "unknown"
	switch {
	case requiredNature && electiveNature:
		requirement = "mixed"
	case requiredNature:
		requirement = "required"
	case electiveNature:
		requirement = "elective"
	case strings.Contains(name, "选修"):
		requirement = "elective"
	case strings.Contains(name, "必修"):
		requirement = "required"
	case hint == "选修":
		requirement = "elective"
	case hint == "必修":
		requirement = "required"
	}
	if requirement == "required" {
		hint = "必修"
	} else if requirement == "elective" {
		hint = "选修"
	}
	children, err := reportRows(row["children"])
	if err != nil {
		return reportNode{}, err
	}
	required, childRequired := Number(row["creditsRequired"]), 0.0
	for _, child := range children {
		childRequired += Number(child["creditsRequired"])
	}
	dual := requirement == "elective" && len(children) > 0 && required > childRequired+1e-9
	childPayloads := []map[string]any{}
	passed, selected, planned, rawPassed, rawSelected := 0.0, 0.0, 0.0, 0.0, 0.0
	childrenCompleted := true
	for _, child := range children {
		node, err := reportCategory(child, path, hint, depth+1)
		if err != nil {
			return reportNode{}, err
		}
		payload := node.payload
		rawPassed += node.rawPassed
		rawSelected += node.rawSelected
		planned += Number(payload["planned_credits"])
		if dual {
			passed += node.rawPassed
			selected += node.rawSelected
			minimum, earned := Number(payload["required_credits"]), node.rawPassed+node.rawSelected
			if minimum > 0 {
				completed := payload["is_completed"] == true
				if node.reported == nil {
					completed = Number(payload["missing_course_count"]) == 0 && Number(payload["missing_group_count"]) == 0
				}
				completed = completed && earned >= minimum
				childrenCompleted = childrenCompleted && completed
				payload["passed_credits"], payload["selected_credits"], payload["earned_credits"] = rounded(node.rawPassed), rounded(node.rawSelected), rounded(earned)
				payload["remaining_credits"], payload["completion_rate"], payload["is_completed"] = rounded(math.Max(0, minimum-earned)), rounded(earned/minimum*100), completed
			}
		} else {
			ratio, minimum, earned := 1.0, Number(payload["required_credits"]), Number(payload["earned_credits"])
			if minimum > 0 && earned > minimum {
				ratio = minimum / earned
			}
			passed += Number(payload["passed_credits"]) * ratio
			selected += Number(payload["selected_credits"]) * ratio
		}
		childPayloads = append(childPayloads, payload)
	}
	constraintEarned := rawPassed + rawSelected
	coursePayloads := []map[string]any{}
	for _, course := range courses {
		payload := reportCourse(course, path, hint)
		coursePayloads = append(coursePayloads, payload)
		credit := Number(payload["credit"])
		if payload["is_passed"] == true {
			rawPassed += credit
			if !dual {
				passed += credit
			}
		} else if payload["is_selected"] == true {
			rawSelected += credit
			if !dual {
				selected += credit
			}
		} else {
			planned += credit
		}
	}
	if !dual {
		constraintEarned = rawPassed + rawSelected
	}
	earned := passed + selected
	if len(children) > 0 && required == 0 {
		passed, selected, earned = 0, 0, 0
	}
	if dual && required > 0 && earned > required {
		passed = math.Min(passed, required)
		selected = math.Min(selected, math.Max(0, required-passed))
		earned = required
	}
	var reported *bool
	completed := earned >= required
	if flag, ok := row["passed"].(bool); ok {
		reported = &flag
		completed = flag
	}
	aggregateRemaining := 0.0
	if dual {
		completed = childrenCompleted && constraintEarned >= required
		aggregateRemaining = math.Max(0, required-constraintEarned)
	}
	joinedPath := strings.Join(path, " > ")
	digest := sha256.Sum256([]byte(joinedPath))
	wid := firstText(row["courseGroupWid"], row["id"], hex.EncodeToString(digest[:])[:16])
	rate := 100.0
	if required > 0 {
		rate = earned / required * 100
	}
	payload := map[string]any{"wid": wid, "name": name, "category_code": Text(row["courseCategory"]), "source_id": Text(row["id"]), "course_group_id": Text(row["courseGroupId"]), "course_group_wid": Text(row["courseGroupWid"]),
		"requirement_type": requirement, "depth": depth, "path": joinedPath, "path_array": path, "is_leaf": len(children) == 0, "has_children": len(children) > 0, "requires_child_minimums_and_total": dual,
		"aggregate_remaining_credits": rounded(aggregateRemaining), "required_credits": rounded(required), "declared_required_credits": rounded(required), "requirement_adjustment": 0,
		"passed_credits": rounded(passed), "selected_credits": rounded(selected), "planned_credits": rounded(planned), "earned_credits": rounded(earned), "remaining_credits": rounded(math.Max(0, required-constraintEarned)),
		"completion_rate": rounded(rate), "is_completed": completed, "pass_required": row["passRequired"] == true, "courses": coursePayloads, "children": childPayloads, "credits_group_judgement": Number(row["creditsCourseGroupJudgement"])}
	for _, field := range []struct{ target, source string }{{"course_count_required", "courseCountRequired"}, {"course_count_taken", "courseCountTaken"}, {"group_count_required", "groupCountRequired"}, {"group_count_taken", "groupCountTaken"}} {
		payload[field.target] = int(Number(row[field.source]))
	}
	payload["missing_course_count"] = math.Max(0, Number(payload["course_count_required"])-Number(payload["course_count_taken"]))
	payload["missing_group_count"] = math.Max(0, Number(payload["group_count_required"])-Number(payload["group_count_taken"]))
	return reportNode{payload: payload, rawPassed: rawPassed, rawSelected: rawSelected, reported: reported}, nil
}

func reportIdentity(nodes []map[string]any, account string, depth int) (string, string, error) {
	if depth > 64 {
		return "", "", &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "培养计划类别层级过深"}
	}
	student, name := "", ""
	for _, node := range nodes {
		for _, field := range []string{"data", "checkCourseVOS"} {
			rows, err := reportRows(node[field])
			if err != nil {
				return "", "", err
			}
			for _, row := range rows {
				identifier := strings.TrimSpace(firstText(row["XH"], row["studentId"]))
				if identifier != "" && identifier != account {
					return "", "", &campus.Error{Code: "ACADEMIC_IDENTITY_MISMATCH", Message: "培养计划账号不匹配，已保留旧缓存"}
				}
				student, name = firstText(student, identifier), firstText(name, row["XM"], row["studentName"])
			}
		}
		children, err := reportRows(node["children"])
		if err != nil {
			return "", "", err
		}
		childStudent, childName, err := reportIdentity(children, account, depth+1)
		if err != nil {
			return "", "", err
		}
		student, name = firstText(student, childStudent), firstText(name, childName)
	}
	return student, name, nil
}

func FetchReport(ctx context.Context, requester Requester, account string) (map[string]any, error) {
	normalized, _, err := FetchReportForExport(ctx, requester, account)
	return normalized, err
}

func FetchReportForExport(ctx context.Context, requester Requester, account string) (map[string]any, map[string]any, error) {
	datas, err := requestData(ctx, requester, http.MethodPost, "https://jwxt.neu.edu.cn/jwapp/sys/byshapp/api/grbg/queryXyzhbx.do", url.Values{"fromPage": {"grxyjcbg"}, "SCLX": {"04"}, "XDLX": {"01"}})
	if err != nil {
		return nil, nil, err
	}
	report := Object(datas["queryXyzhbx"])
	normalized, err := normalizeReport(report, account)
	if err != nil {
		return nil, nil, err
	}
	return normalized, report, nil
}

func normalizeReport(report map[string]any, account string) (map[string]any, error) {
	program := Object(report["fanbx"])
	if program == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "培养计划缺少报告主体，已保留旧缓存"}
	}
	children, err := reportRows(program["children"])
	if err != nil {
		return nil, err
	}
	student, name, err := reportIdentity(children, account, 0)
	if err != nil {
		return nil, err
	}
	student = firstText(student, account)
	grade := ""
	if len(student) >= 4 {
		if _, err := strconv.Atoi(student[:4]); err == nil {
			grade = student[:4]
		}
	}
	categories := []map[string]any{}
	passed, selected, childRequired := 0.0, 0.0, 0.0
	for _, child := range children {
		node, err := reportCategory(child, nil, "", 0)
		if err != nil {
			return nil, err
		}
		categories = append(categories, node.payload)
		passed += Number(node.payload["passed_credits"])
		selected += Number(node.payload["selected_credits"])
		childRequired += Number(node.payload["required_credits"])
	}
	required := Number(program["creditsRequired"])
	if required == 0 {
		required = childRequired
	}
	earned, rate := passed+selected, 0.0
	if required > 0 {
		rate = earned / required * 100
	}
	outside := []map[string]any{}
	for _, source := range []any{program["outsideProgramCourses"], report["fawbx"], report["fawbxMap"]} {
		rows, err := reportRows(source)
		if err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			continue
		}
		for _, row := range rows {
			identifier := strings.TrimSpace(firstText(row["XH"], row["studentId"]))
			if identifier != "" && identifier != account {
				return nil, &campus.Error{Code: "ACADEMIC_IDENTITY_MISMATCH", Message: "方案外课程账号不匹配"}
			}
			outside = append(outside, reportCourse(row, []string{"方案外课程"}, ""))
		}
		break
	}
	return map[string]any{"student_name": name, "student_id": student, "grade": grade, "college": "", "major": "", "class_name": "", "expected_graduation": "",
		"program_code": Text(program["educationalProgramCode"]), "program_name": firstText(program["educationalProgramName"], program["name"]), "calculated_time": Text(report["calculatedTime"]),
		"credit_summary": map[string]any{"total_required": required, "total_passed": rounded(passed), "total_selected": rounded(selected), "total_earned": rounded(earned), "total_remaining": rounded(math.Max(0, required-earned)), "completion_rate": rounded(rate)}, "categories": categories, "outside_courses": outside}, nil
}

func ReportSemantic(payload map[string]any) (json.RawMessage, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	copy, err := Decode(encoded)
	if err != nil {
		return nil, err
	}
	copy["calculated_time"] = ""
	var normalize func(map[string]any)
	normalize = func(node map[string]any) {
		for _, field := range []string{"categories", "children", "courses", "outside_courses"} {
			rows, ok := node[field].([]any)
			if !ok {
				continue
			}
			for _, raw := range rows {
				if child := Object(raw); child != nil {
					normalize(child)
				}
			}
			sort.SliceStable(rows, func(left, right int) bool {
				first, _ := json.Marshal(rows[left])
				second, _ := json.Marshal(rows[right])
				return string(first) < string(second)
			})
		}
	}
	normalize(copy)
	return json.Marshal(copy)
}
