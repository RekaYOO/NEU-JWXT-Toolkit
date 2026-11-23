package academic

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

const academicBase = "https://jwxt.neu.edu.cn/jwapp/sys/cjzhcxapp/"

type Requester interface {
	Request(context.Context, string, string, url.Values) ([]byte, error)
}

type Score struct {
	Name            string  `json:"name"`
	Code            string  `json:"code"`
	Score           string  `json:"score"`
	ScoreValue      float64 `json:"score_value"`
	GPA             float64 `json:"gpa"`
	Credit          float64 `json:"credit"`
	Term            string  `json:"term"`
	TermDisplay     string  `json:"term_display"`
	CourseType      string  `json:"course_type"`
	CourseCategory  string  `json:"course_category"`
	GeneralCategory string  `json:"general_category"`
	ExamType        string  `json:"exam_type"`
	ExamStatus      string  `json:"exam_status"`
	CourseNature    string  `json:"course_nature"`
	Passed          bool    `json:"is_passed"`
	GradingScale    string  `json:"grading_scale"`
	DetailRef       string  `json:"detail_ref,omitempty"`
}

type Scores struct {
	Scores     []Score  `json:"scores"`
	OverallGPA *float64 `json:"overall_gpa"`
}

func Text(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func Number(value any) float64 {
	number, err := strconv.ParseFloat(strings.TrimSpace(Text(value)), 64)
	if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
		return 0
	}
	return number
}

func Object(value any) map[string]any { result, _ := value.(map[string]any); return result }

func Decode(payload []byte) (map[string]any, error) {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	var result map[string]any
	if err := decoder.Decode(&result); err != nil || result == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校响应不是有效数据，已保留旧缓存"}
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校响应包含多余数据"}
	}
	return result, nil
}

func requestData(ctx context.Context, requester Requester, method, target string, form url.Values) (map[string]any, error) {
	payload, err := requester.Request(ctx, method, target, form)
	if err != nil {
		return nil, err
	}
	result, err := Decode(payload)
	if err != nil {
		return nil, err
	}
	if Text(result["code"]) != "0" {
		return nil, &campus.Error{Code: "ACADEMIC_RESPONSE_REJECTED", Message: "学校未确认查询成功，已保留旧缓存"}
	}
	datas := Object(result["datas"])
	if datas == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校响应缺少数据字段"}
	}
	return datas, nil
}

func scoreFromRow(row map[string]any) Score {
	score := Score{Name: Text(row["KCM"]), Code: Text(row["KCH"]), Score: strings.TrimSpace(Text(row["XSZCJ"])), GPA: Number(row["JD"]), Credit: Number(row["XF"]), Term: Text(row["XNXQDM"]), TermDisplay: Text(row["XNXQDM_DISPLAY"]), CourseType: Text(row["KCXZDM_DISPLAY"]), CourseCategory: Text(row["KCLBDM_DISPLAY"]), GeneralCategory: Text(row["XGXKLBDM_DISPLAY"]), ExamType: Text(row["KSLXDM_DISPLAY"]), ExamStatus: Text(row["CXCKDM_DISPLAY"]), CourseNature: Text(row["KCXZDM"]), Passed: Text(row["SFJG_DISPLAY"]) == "是", DetailRef: Text(row["WID"])}
	if score.TermDisplay == "" {
		score.TermDisplay = score.Term
	}
	numeric, err := strconv.ParseFloat(score.Score, 64)
	if err == nil && !math.IsInf(numeric, 0) && !math.IsNaN(numeric) {
		score.ScoreValue = numeric
		if score.GPA != 0 && math.Abs((numeric-50)/10-score.GPA) >= 0.3 {
			score.ScoreValue = (score.GPA + 5) * 10
		}
	} else if score.GPA != 0 {
		score.ScoreValue = (score.GPA + 5) * 10
	}
	return score
}

func FetchScores(ctx context.Context, requester Requester) (Scores, error) {
	result := Scores{Scores: []Score{}}
	datas, err := requestData(ctx, requester, http.MethodGet, academicBase+"modules/wdcj/cxwdcjxnxq.do", nil)
	if err != nil {
		return result, err
	}
	terms, ok := Object(datas["cxwdcjxnxq"])["rows"].([]any)
	if !ok {
		return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学期列表结构异常"}
	}
	seenTerms := map[string]bool{}
	for _, rawTerm := range terms {
		term := Text(Object(rawTerm)["XNXQDM"])
		if term == "" {
			return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学期记录缺少标识"}
		}
		if seenTerms[term] {
			continue
		}
		seenTerms[term] = true
		seenCount := 0
		expectedTotal := -1
		for page := 1; page <= 100; page++ {
			datas, err := requestData(ctx, requester, http.MethodPost, academicBase+"modules/wdcj/cxwdcj.do", url.Values{"XNXQDM": {term}, "pageSize": {"500"}, "pageNumber": {strconv.Itoa(page)}})
			if err != nil {
				return result, err
			}
			section := Object(datas["cxwdcj"])
			rows, ok := section["rows"].([]any)
			if !ok {
				return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "成绩列表结构异常"}
			}
			for _, value := range rows {
				row := Object(value)
				if row == nil {
					return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "成绩行结构异常"}
				}
				result.Scores = append(result.Scores, scoreFromRow(row))
			}
			total := 0
			if rawTotal, exists := section["totalSize"]; exists {
				parsed, parseErr := strconv.ParseInt(Text(rawTotal), 10, 32)
				if parseErr != nil || parsed < 0 {
					return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校成绩总数字段无效"}
				}
				total = int(parsed)
				if total == 0 && (len(rows) > 0 || expectedTotal > 0) {
					return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校成绩总数与分页不一致"}
				}
			} else if expectedTotal >= 0 {
				return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校成绩分页缺少总数"}
			}
			seenCount += len(rows)
			if total > 0 {
				if expectedTotal >= 0 && expectedTotal != total {
					return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "读取期间学校成绩总数发生变化，请重新刷新"}
				}
				expectedTotal = total
				if seenCount > total || (seenCount < total && len(rows) == 0) {
					return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校成绩分页不完整"}
				}
			}
			if (total > 0 && seenCount == total) || (total == 0 && len(rows) < 500) {
				break
			}
			if page == 100 {
				return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "成绩分页超过允许范围"}
			}
		}
	}
	datas, err = requestData(ctx, requester, http.MethodGet, academicBase+"api/wdcj/queryPjxfjd.do", nil)
	if err != nil {
		return result, err
	}
	official := Object(datas["queryPjxfjd"])
	if official == nil {
		return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "官方绩点结构异常"}
	}
	if value := strings.TrimSpace(Text(official["ZPJXFJD"])); value != "" {
		number, err := strconv.ParseFloat(value, 64)
		if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
			return result, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "官方绩点字段无效"}
		}
		result.OverallGPA = &number
	}
	sort.SliceStable(result.Scores, func(left, right int) bool {
		first, second := result.Scores[left], result.Scores[right]
		first.DetailRef, second.DetailRef = "", ""
		firstKey, _ := json.Marshal(first)
		secondKey, _ := json.Marshal(second)
		return bytes.Compare(firstKey, secondKey) < 0
	})
	return result, nil
}

func (scores Scores) Semantic() Scores {
	result := Scores{OverallGPA: scores.OverallGPA, Scores: append([]Score{}, scores.Scores...)}
	for index := range result.Scores {
		result.Scores[index].DetailRef = ""
	}
	return result
}

func FetchScoreDetail(ctx context.Context, requester Requester, score Score) (map[string]any, error) {
	if score.DetailRef == "" {
		return nil, &campus.Error{Code: "DETAIL_REFERENCE_MISSING", Message: "课程详情标识不可用，请刷新总成绩"}
	}
	datas, err := requestData(ctx, requester, http.MethodPost, academicBase+"api/wdcj/details.do", url.Values{"WID": {score.DetailRef}})
	if err != nil {
		return nil, err
	}
	details := Object(datas["details"])
	if details == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "成绩详情结构异常"}
	}
	items := []map[string]any{}
	if rows, ok := details["itemScores"].([]any); ok {
		for _, raw := range rows {
			item := Object(raw)
			if item == nil {
				continue
			}
			value := item["value"]
			switch value.(type) {
			case nil, string, json.Number, bool:
			default:
				value = ""
			}
			var passed any
			if flag, ok := item["pass"].(bool); ok {
				passed = flag
			}
			items = append(items, map[string]any{"code": Text(item["code"]), "name": Text(item["name"]), "value": value, "pass": passed, "highest_score_in_proportion": item["highestScoreInProportion"] == true})
		}
	}
	var passed any
	if flag, ok := details["pass"].(bool); ok {
		passed = flag
	}
	return map[string]any{"course_code": score.Code, "term": score.Term, "source_score": score.Score, "source_gpa": score.GPA, "score": Text(details["score"]), "grade_point": Text(details["gradePoint"]), "pass": passed, "item_scores": items}, nil
}
