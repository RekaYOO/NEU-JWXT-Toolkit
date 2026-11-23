package academic

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"net/url"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type EvaluationRequester interface {
	EvaluationRequest(context.Context, string, json.RawMessage) ([]byte, error)
}

func ValidEvaluationID(value string) bool {
	if strings.TrimSpace(value) == "" || utf8.RuneCountInString(value) > 128 {
		return false
	}
	for _, character := range value {
		if unicode.IsControl(character) {
			return false
		}
	}
	return true
}

func evaluationInvalid() error {
	return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "评教数据结构异常，请刷新后重试"}
}

func evaluationData(ctx context.Context, requester EvaluationRequester, path string, body any) (any, error) {
	encoded, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	payload, err := requester.EvaluationRequest(ctx, "/api/"+path, encoded)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	var result, trailing any
	if decoder.Decode(&result) != nil || decoder.Decode(&trailing) != io.EOF {
		return nil, evaluationInvalid()
	}
	return result, nil
}

func evaluationRows(value any) ([]map[string]any, error) {
	if _, ok := value.([]any); !ok {
		return nil, evaluationInvalid()
	}
	rows, err := reportRows(value)
	if err != nil {
		return nil, evaluationInvalid()
	}
	return rows, nil
}

func FetchEvaluationCycles(ctx context.Context, requester EvaluationRequester) (map[string]any, error) {
	data, err := evaluationData(ctx, requester, "qsmart/datacenter/TSysCycleController/tSysCycle/findCycle", nil)
	if err != nil {
		return nil, err
	}
	rows, err := evaluationRows(data)
	if err != nil {
		return nil, err
	}
	cycles := []map[string]any{}
	selected := ""
	for _, row := range rows {
		value := Text(row["value"])
		if !ValidEvaluationID(value) {
			return nil, evaluationInvalid()
		}
		isDefault := Number(row["isdefault"]) == 1 || row["isdefault"] == true
		flag := 0
		if isDefault {
			flag = 1
			selected = value
		}
		cycles = append(cycles, map[string]any{"cycleid": Text(row["cycleid"]), "value": value, "isdefault": flag})
	}
	if selected == "" && len(cycles) > 0 {
		selected = Text(cycles[0]["value"])
	}
	return map[string]any{"cycles": cycles, "default": selected}, nil
}

func evaluationCycle(ctx context.Context, requester EvaluationRequester, cycle string) (string, error) {
	if cycle != "" {
		if !ValidEvaluationID(cycle) || utf8.RuneCountInString(cycle) > 32 {
			return "", &campus.Error{Code: "INVALID_REQUEST", Message: "评教学期无效"}
		}
		return cycle, nil
	}
	data, err := FetchEvaluationCycles(ctx, requester)
	if err != nil {
		return "", err
	}
	cycle = Text(data["default"])
	if cycle == "" {
		return "", &campus.Error{Code: "EVALUATION_CYCLE_UNAVAILABLE", Message: "暂无可用评教学期"}
	}
	return cycle, nil
}

func evaluationList(ctx context.Context, requester EvaluationRequester, path string, body map[string]any, idField string) ([]map[string]any, error) {
	result := []map[string]any{}
	seen := map[string]bool{}
	expected := -1
	for page := 1; page <= 200; page++ {
		body["page"] = map[string]any{"conditions": "", "custom": map[string]any{}, "orderBy": "", "pageIndex": page, "pageSize": 100, "paginationEnable": true}
		data, err := evaluationData(ctx, requester, path, body)
		if err != nil {
			return nil, err
		}
		object := Object(data)
		if object == nil {
			return nil, evaluationInvalid()
		}
		rows, err := evaluationRows(object["items"])
		if err != nil {
			return nil, err
		}
		total, err := strconv.Atoi(Text(object["rowCount"]))
		if err != nil || total < 0 || total > 20000 {
			return nil, evaluationInvalid()
		}
		if expected >= 0 && total != expected {
			return nil, evaluationInvalid()
		}
		expected = total
		for _, row := range rows {
			id := Text(row[idField])
			if !ValidEvaluationID(id) || seen[id] {
				return nil, evaluationInvalid()
			}
			seen[id] = true
			result = append(result, row)
		}
		if len(result) == total {
			return result, nil
		}
		if len(result) > total || len(rows) == 0 {
			return nil, evaluationInvalid()
		}
	}
	return nil, evaluationInvalid()
}

func FetchEvaluationTasks(ctx context.Context, requester EvaluationRequester, cycle string) (map[string]any, error) {
	cycle, err := evaluationCycle(ctx, requester, cycle)
	if err != nil {
		return nil, err
	}
	rows, err := evaluationList(ctx, requester, "TSysDdJxapController/tSysDdJxap/findJdpXqByTaskidHZ", map[string]any{"xnxq": cycle}, "taskid")
	if err != nil {
		return nil, err
	}
	tasks := []map[string]any{}
	for _, row := range rows {
		tasks = append(tasks, map[string]any{"task_id": Text(row["taskid"]), "task_name": Text(row["taskname"]), "total_count": int(Number(row["count"])), "evaluated_count": int(Number(row["ypcount"])), "pending_count": int(Number(row["wpcount"])), "status": int(Number(row["status"]))})
	}
	return map[string]any{"tasks": tasks, "total": len(tasks), "xnxq": cycle}, nil
}

func evaluationCourses(ctx context.Context, requester EvaluationRequester, task, cycle string) ([]map[string]any, string, error) {
	if !ValidEvaluationID(task) {
		return nil, "", &campus.Error{Code: "INVALID_REQUEST", Message: "评教任务无效"}
	}
	cycle, err := evaluationCycle(ctx, requester, cycle)
	if err != nil {
		return nil, "", err
	}
	rows, err := evaluationList(ctx, requester, "TSysDdJxapController/tSysDdJxap/findJdpXqByTaskid", map[string]any{"xnxq": cycle, "taskid": task, "kcmc": "", "jgxm": ""}, "xspjid")
	if err != nil {
		return nil, "", err
	}
	for _, row := range rows {
		if Text(row["taskid"]) != task || (Text(row["issubmit"]) != "0" && Text(row["issubmit"]) != "1") {
			return nil, "", evaluationInvalid()
		}
		if value := Text(row["xnxqid"]); value != "" && value != cycle {
			return nil, "", evaluationInvalid()
		}
	}
	return rows, cycle, nil
}

func FetchEvaluationCourses(ctx context.Context, requester EvaluationRequester, task, cycle string) (map[string]any, error) {
	rows, _, err := evaluationCourses(ctx, requester, task, cycle)
	if err != nil {
		return nil, err
	}
	courses := []map[string]any{}
	completed := 0
	for _, row := range rows {
		evaluated := Text(row["issubmit"]) == "0"
		if evaluated {
			completed++
		}
		courses = append(courses, map[string]any{"xspjid": Text(row["xspjid"]), "task_id": task, "task_name": Text(row["taskname"]), "course_name": Text(row["kcmc"]), "teacher_name": Text(row["jgxm"]), "teacher_code": Text(row["jg0101id"]), "department": Text(row["kkdwbm"]), "department_id": firstText(row["kkdwid"], row["dwid"]), "course_type_name": Text(row["kcsxname"]), "is_submit": Text(row["issubmit"]), "is_evaluated": evaluated, "is_kpj": Text(row["iskpj"]), "score": row["score"], "avg_score": row["zpf"]})
	}
	return map[string]any{"task_id": task, "courses": courses, "total": len(courses), "pending": len(courses) - completed, "completed": completed}, nil
}

func evaluationGrades(value any) any {
	if value == nil || Text(value) == "" {
		return nil
	}
	var values []any
	switch typed := value.(type) {
	case []any:
		values = typed
	case string:
		if strings.Contains(typed, ",") {
			for _, part := range strings.Split(typed, ",") {
				values = append(values, strings.TrimSpace(part))
			}
		}
	}
	if values != nil {
		result := []int{}
		for _, part := range values {
			grade, err := strconv.Atoi(Text(part))
			if err != nil || grade < 1 || grade > 6 {
				return nil
			}
			result = append(result, grade)
		}
		if len(result) == 0 {
			return nil
		}
		return result
	}
	grade, err := strconv.Atoi(Text(value))
	if err != nil || grade < 1 || grade > 6 {
		return nil
	}
	return grade
}

func FetchEvaluationIndicators(ctx context.Context, requester EvaluationRequester, task, record, cycle string) (map[string]any, error) {
	rows, cycle, err := evaluationCourses(ctx, requester, task, cycle)
	if err != nil {
		return nil, err
	}
	var course map[string]any
	for _, row := range rows {
		if Text(row["xspjid"]) == record {
			course = row
			break
		}
	}
	if course == nil {
		return nil, &campus.Error{Code: "EVALUATION_COURSE_MISSING", Message: "该评教任务中没有此课程"}
	}
	return evaluationTarget(ctx, requester, task, record, cycle, Text(course["libid"]))
}

func evaluationTarget(ctx context.Context, requester EvaluationRequester, task, record, cycle, library string) (map[string]any, error) {
	data, err := evaluationData(ctx, requester, "TSysDdKbxxController/getTasklib/"+url.PathEscape(task), map[string]any{})
	if err != nil {
		return nil, err
	}
	libraries, err := evaluationRows(data)
	if err != nil {
		return nil, err
	}
	var chosen map[string]any
	for _, item := range libraries {
		if library != "" && Text(item["id"]) == library {
			chosen = item
			break
		}
	}
	if chosen == nil && library == "" && len(libraries) == 1 {
		chosen = libraries[0]
	}
	if chosen == nil || !ValidEvaluationID(Text(chosen["id"])) {
		return nil, evaluationInvalid()
	}
	params := url.Values{"libid": {Text(chosen["id"])}, "tkid": {""}, "taskid": {record}, "xnxqid": {cycle}, "type": {"student_jdp"}, "jxapid": {""}, "jg0101id": {""}, "libtaskid": {task}, "kkdwid": {""}, "kclbcode": {""}}
	data, err = evaluationData(ctx, requester, "TSysDdEvalTargetController/queryEvalTargetTreeForEval?"+params.Encode(), nil)
	if err != nil {
		return nil, err
	}
	object := Object(data)
	if object == nil {
		return nil, evaluationInvalid()
	}
	rows, err := evaluationRows(object["list"])
	if err != nil || len(rows) == 0 {
		return nil, evaluationInvalid()
	}
	indicators := []map[string]any{}
	selection, scored := 0, 0
	total := 0.0
	seen := map[string]bool{}
	for _, row := range rows {
		id := Text(row["zbid"])
		if !ValidEvaluationID(id) || seen[id] {
			return nil, evaluationInvalid()
		}
		seen[id] = true
		levels := row["leveljson"]
		if text, ok := levels.(string); ok {
			if text == "" {
				levels = []any{}
			} else {
				if json.Unmarshal([]byte(text), &levels) != nil {
					return nil, evaluationInvalid()
				}
			}
		}
		if levels == nil {
			levels = []any{}
		}
		options, err := evaluationRows(levels)
		if err != nil {
			return nil, err
		}
		kind := int(Number(row["evaltype"]))
		pointsByGrade := map[int]float64{}
		for _, option := range options {
			grade := int(Number(option["value"]))
			if option["df"] != nil {
				pointsByGrade[grade] = Number(option["df"])
			}
		}
		gradeScore := func(grade int) float64 {
			if score, found := pointsByGrade[grade]; found {
				return score
			}
			return float64(40 + grade*10)
		}
		if kind == 1 {
			selection++
		}
		grade := evaluationGrades(row["dfdj"])
		var score any
		switch value := grade.(type) {
		case int:
			score = gradeScore(value)
			total += score.(float64)
			scored++
		case []int:
			points := []float64{}
			highest := 0.0
			for _, number := range value {
				point := gradeScore(number)
				points = append(points, point)
				if point > highest {
					highest = point
				}
			}
			score = points
			total += highest
			scored++
		}
		indicators = append(indicators, map[string]any{"zbid": id, "zbmc": Text(row["zbmc"]), "evaltype": kind, "sfdx": int(Number(row["sfdx"])), "sfbt": int(Number(row["sfbt"])), "weight": Number(row["weight"]), "fz": Number(row["fz"]), "jsjx": Text(row["jsjx"]), "sort": int(Number(row["sort"])), "level_json": options, "parent_id": firstText(row["fzbid"], "root"), "dfdj": grade, "result": firstText(row["result"], row["subjectContent"]), "score": score})
	}
	var average any
	if scored > 0 {
		average = math.Round(float64(total)*10/float64(scored)) / 10
	}
	return map[string]any{"libid": Text(chosen["id"]), "libname": Text(chosen["libname"]), "preface": Text(object["preface"]), "total_score": Number(object["zbkzf"]), "indicators": indicators, "total_indicators": len(indicators), "selection_count": selection, "text_count": len(indicators) - selection, "avg_score": average, "scored_count": scored}, nil
}
