package academic

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type EvaluationInput struct {
	Task     string                     `json:"task_id"`
	Record   string                     `json:"xspjid"`
	Cycle    string                     `json:"xnxq,omitempty"`
	DryRun   *bool                      `json:"dry_run"`
	Strategy string                     `json:"strategy"`
	Scores   map[string]json.RawMessage `json:"custom_scores"`
	Texts    map[string]string          `json:"text_results"`
	Reason   string                     `json:"reason,omitempty"`
}

type PreparedEvaluation struct {
	Course           map[string]any
	Cycle            string
	Payload          json.RawMessage
	Summary          map[string]any
	AlreadySubmitted bool
}

func evaluationValidation(message string) error {
	return &campus.Error{Code: "INVALID_REQUEST", Message: message}
}

func (input EvaluationInput) Preview() bool { return input.DryRun == nil || *input.DryRun }

func (input EvaluationInput) Validate() error {
	if !ValidEvaluationID(input.Task) || !ValidEvaluationID(input.Record) || (input.Cycle != "" && (!ValidEvaluationID(input.Cycle) || utf8.RuneCountInString(input.Cycle) > 32)) {
		return evaluationValidation("评教任务或课程标识无效")
	}
	if input.Strategy != "" && input.Strategy != "highest" && input.Strategy != "lowest" && input.Strategy != "custom" {
		return evaluationValidation("评分策略无效")
	}
	if len(input.Scores) > 100 || len(input.Texts) > 50 || utf8.RuneCountInString(input.Reason) > 2000 {
		return evaluationValidation("评教内容超过允许长度")
	}
	for id, raw := range input.Scores {
		if !ValidEvaluationID(id) {
			return evaluationValidation("评分指标标识无效")
		}
		if _, err := evaluationSelection(raw); err != nil {
			return err
		}
	}
	for id, text := range input.Texts {
		if !ValidEvaluationID(id) || utf8.RuneCountInString(text) > 2000 {
			return evaluationValidation("文本指标标识或长度无效")
		}
	}
	return nil
}

func evaluationSelection(raw json.RawMessage) ([]int, error) {
	var values []int
	if len(raw) > 0 && raw[0] == '[' {
		if json.Unmarshal(raw, &values) != nil {
			return nil, evaluationValidation("评分必须为整数等级")
		}
	} else {
		var value int
		if json.Unmarshal(raw, &value) != nil {
			return nil, evaluationValidation("评分必须为整数等级")
		}
		values = []int{value}
	}
	if len(values) < 1 || len(values) > 6 {
		return nil, evaluationValidation("请选择有效评分")
	}
	seen := map[int]bool{}
	for _, value := range values {
		if value < 1 || value > 6 || seen[value] {
			return nil, evaluationValidation("评分等级无效或重复")
		}
		seen[value] = true
	}
	return values, nil
}

func FindEvaluationCourse(ctx context.Context, requester EvaluationRequester, task, record, cycle string) (map[string]any, string, error) {
	rows, cycle, err := evaluationCourses(ctx, requester, task, cycle)
	if err != nil {
		return nil, "", err
	}
	for _, row := range rows {
		if Text(row["xspjid"]) == record {
			return row, cycle, nil
		}
	}
	return nil, "", &campus.Error{Code: "EVALUATION_COURSE_MISSING", Message: "该评教任务中没有此课程"}
}

func PrepareEvaluation(ctx context.Context, requester EvaluationRequester, account string, input EvaluationInput) (PreparedEvaluation, error) {
	if err := input.Validate(); err != nil {
		return PreparedEvaluation{}, err
	}
	course, cycle, err := FindEvaluationCourse(ctx, requester, input.Task, input.Record, input.Cycle)
	if err != nil {
		return PreparedEvaluation{}, err
	}
	prepared := PreparedEvaluation{Course: course, Cycle: cycle, AlreadySubmitted: Text(course["issubmit"]) == "0"}
	if owner := Text(course["pjrid"]); owner != "" && owner != account {
		return PreparedEvaluation{}, &campus.Error{Code: "ACADEMIC_IDENTITY_MISMATCH", Message: "评教课程与当前账号不一致"}
	}
	if prepared.AlreadySubmitted {
		return prepared, nil
	}
	if Text(course["iskpj"]) != "1" {
		return PreparedEvaluation{}, evaluationValidation("该课程当前不可评教")
	}
	now := time.Now().UnixMilli()
	for _, field := range []string{"begindate", "enddate"} {
		if course[field] != nil && Text(course[field]) != "" {
			stamp, parseErr := strconv.ParseInt(Text(course[field]), 10, 64)
			if parseErr != nil || stamp < 0 {
				return PreparedEvaluation{}, evaluationInvalid()
			}
			if stamp > 0 && ((field == "begindate" && now < stamp) || (field == "enddate" && now > stamp)) {
				return PreparedEvaluation{}, evaluationValidation("当前不在该课程评教时间内")
			}
		}
	}
	target, err := evaluationTarget(ctx, requester, input.Task, input.Record, cycle, Text(course["libid"]))
	if err != nil {
		return PreparedEvaluation{}, err
	}
	payload, summary, err := BuildEvaluationSubmission(course, target, cycle, input)
	if err != nil {
		return PreparedEvaluation{}, err
	}
	prepared.Payload, prepared.Summary = payload, summary
	return prepared, nil
}

func BuildEvaluationSubmission(course, target map[string]any, cycle string, input EvaluationInput) (json.RawMessage, map[string]any, error) {
	indicators, ok := target["indicators"].([]map[string]any)
	if !ok || len(indicators) == 0 {
		return nil, nil, evaluationInvalid()
	}
	known := map[string]int{}
	for _, indicator := range indicators {
		known[Text(indicator["zbid"])] = int(Number(indicator["evaltype"]))
	}
	for id := range input.Scores {
		if known[id] != 1 {
			return nil, nil, evaluationValidation("评分包含不属于当前课程的指标")
		}
	}
	for id := range input.Texts {
		if kind, found := known[id]; !found || kind == 1 {
			return nil, nil, evaluationValidation("文本包含不属于当前课程的指标")
		}
	}
	strategy := input.Strategy
	if strategy == "" {
		strategy = "highest"
	}
	results := []map[string]any{}
	selectionCount, totalScore, textCount := 0, 0.0, 0
	effective := []int{}
	for _, indicator := range indicators {
		id := Text(indicator["zbid"])
		kind := int(Number(indicator["evaltype"]))
		grade, score, text := "", 0.0, ""
		if kind == 1 {
			options, ok := indicator["level_json"].([]map[string]any)
			if !ok || len(options) == 0 {
				return nil, nil, evaluationValidation("官方评分选项不可用，请刷新指标")
			}
			points := map[int]float64{}
			grades := []int{}
			for _, option := range options {
				value, err := strconv.Atoi(Text(option["value"]))
				if err != nil || value < 1 || value > 6 {
					return nil, nil, evaluationInvalid()
				}
				if _, exists := points[value]; exists {
					return nil, nil, evaluationInvalid()
				}
				point := float64(40 + value*10)
				if option["df"] != nil {
					number, err := strconv.ParseFloat(Text(option["df"]), 64)
					if err != nil || math.IsNaN(number) || math.IsInf(number, 0) || number < 0 {
						return nil, nil, evaluationInvalid()
					}
					point = number
				}
				points[value] = point
				grades = append(grades, value)
			}
			sort.Slice(grades, func(left, right int) bool {
				if points[grades[left]] == points[grades[right]] {
					return grades[left] < grades[right]
				}
				return points[grades[left]] < points[grades[right]]
			})
			chosen := []int{}
			if strategy == "custom" {
				if raw, exists := input.Scores[id]; exists {
					var err error
					chosen, err = evaluationSelection(raw)
					if err != nil {
						return nil, nil, err
					}
				}
			} else {
				index := len(grades) - 1
				if strategy == "lowest" {
					index = 0
				}
				if selectionCount == 0 && len(grades) > 1 {
					if strategy == "lowest" {
						index = 1
					} else {
						index--
					}
				}
				chosen = []int{grades[index]}
			}
			if len(chosen) > 1 && Number(indicator["sfdx"]) != 1 {
				return nil, nil, evaluationValidation("单选指标不能选择多个等级")
			}
			if len(chosen) == 0 && Number(indicator["sfbt"]) == 1 {
				return nil, nil, evaluationValidation("必填指标未评分")
			}
			best := 0
			for _, value := range chosen {
				point, exists := points[value]
				if !exists {
					return nil, nil, evaluationValidation("评分不在官方选项中")
				}
				if best == 0 || point > score || (point == score && value > best) {
					best, score = value, point
				}
			}
			if best != 0 {
				grade = strconv.Itoa(best)
				effective = append(effective, best)
			}
			selectionCount++
			totalScore += score
		} else {
			text = input.Texts[id]
			textCount++
			if Number(indicator["sfbt"]) == 1 && strings.TrimSpace(text) == "" {
				return nil, nil, evaluationValidation("必填文本指标未填写")
			}
		}
		results = append(results, map[string]any{"id": "", "taskid": input.Record, "zbid": id, "subjectContent": text, "xnxqid": cycle, "zblx": "", "rwid": input.Task, "kkdwid": firstText(course["kkdwid"], course["dwid"]), "dwid": firstText(course["kkdwid"], course["dwid"]), "libid": target["libid"], "evaltype": kind, "mlid": "", "mlmc": "", "pjsjid": Text(course["pjsjid"]), "cpztcode": firstText(course["cpztcode"], "1"), "issubmit": 0, "score": score, "dfdj": grade})
	}
	if len(effective) > 1 {
		same := true
		for _, grade := range effective[1:] {
			if grade != effective[0] {
				same = false
			}
		}
		if same {
			return nil, nil, evaluationValidation("评价选项不能全部相同")
		}
	}
	average := 0.0
	if selectionCount > 0 {
		average = math.Round(totalScore*10/float64(selectionCount)) / 10
	}
	payload, err := json.Marshal(map[string]any{"resultList": results, "task": map[string]any{"id": input.Record, "zpf": average, "issave": "0", "issubmit": 0, "libid": target["libid"], "dfyy": input.Reason, "pjsc": int(Number(course["pjsc"]))}})
	return payload, map[string]any{"average_score": average, "indicator_count": len(results), "selection_count": selectionCount, "text_count": textCount}, err
}

func SendEvaluation(ctx context.Context, requester EvaluationRequester, payload json.RawMessage) string {
	response, err := requester.EvaluationRequest(ctx, "/api/TSysDdEvalResultJdpController/tSysDdEvalResultJdp/saveJdp", payload)
	if err != nil {
		var failure *campus.Error
		if errors.As(err, &failure) && failure.Code == "EVALUATION_NOT_SENT" {
			return "not_sent"
		}
		return "unknown"
	}
	var result any
	if json.Unmarshal(response, &result) != nil {
		return "unknown"
	}
	if result == "success" {
		return "confirmed"
	}
	if object := Object(result); object != nil {
		message := Text(object["message"])
		if strings.Contains(message, "全部相同") {
			return "rejected"
		}
	}
	return "unknown"
}
