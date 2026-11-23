package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"golang.org/x/net/html"
)

type JWXKDetail struct {
	Batch  string `json:"batch_code"`
	Scope  string `json:"teaching_class_type"`
	Course string `json:"course_code"`
	Class  string `json:"class_id"`
}

func (input JWXKDetail) Valid() bool {
	return jwxkBatchCode.MatchString(input.Batch) && jwxkBatchCode.MatchString(input.Course) && jwxkBatchCode.MatchString(input.Class) && jwxkScopeCode.MatchString(input.Scope) && input.Scope != "ALL" && input.Scope != "ROUND"
}

var jwxkDetailLabels = map[string]string{
	"课程代码": "course_code", "课程号": "course_code", "课程编号": "course_code", "课程名称": "course_name", "中文名称": "course_name", "英文名称": "english_name",
	"学分": "credits", "学时": "hours", "总学时": "hours", "开课单位": "department", "开课学院": "department", "学院": "department",
	"课程性质": "course_nature", "课程类别": "course_category", "通识选修课类别": "general_elective_category", "通识选修类别": "general_elective_category",
	"考试类型": "exam_type", "考核方式": "exam_type", "成绩分制": "score_scale", "成绩记载方式": "score_scale", "课程简介": "description", "课程介绍": "description",
}

func ParseJWXKDetailHTML(source string) map[string]string {
	result := map[string]string{}
	root, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return result
	}
	var text func(*html.Node) string
	text = func(node *html.Node) string {
		if node.Type == html.TextNode {
			return node.Data
		}
		if node.Type == html.ElementNode && (node.Data == "script" || node.Data == "style") {
			return ""
		}
		parts := []string{}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			parts = append(parts, text(child))
		}
		return strings.Join(parts, " ")
	}
	accept := func(label, value *html.Node) {
		key := jwxkDetailLabels[strings.NewReplacer(":", "", "：", "").Replace(strings.Join(strings.Fields(text(label)), ""))]
		if key != "" && result[key] == "" {
			result[key] = strings.Join(strings.Fields(text(value)), " ")
		}
	}
	var visit func(*html.Node)
	visit = func(node *html.Node) {
		if node.Type == html.ElementNode {
			if node.Data == "tr" {
				cells := []*html.Node{}
				for child := node.FirstChild; child != nil; child = child.NextSibling {
					if child.Type == html.ElementNode && (child.Data == "td" || child.Data == "th") {
						cells = append(cells, child)
					}
				}
				for index := 0; index+1 < len(cells); index += 2 {
					accept(cells[index], cells[index+1])
				}
			} else if node.Data == "dt" || node.Data == "label" {
				for sibling := node.NextSibling; sibling != nil; sibling = sibling.NextSibling {
					if sibling.Type == html.ElementNode && (node.Data == "label" || sibling.Data == "dd") {
						accept(node, sibling)
						break
					}
				}
			}
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	visit(root)
	return result
}

func FetchJWXKDetail(ctx context.Context, requester ServiceRequester, mode string, input JWXKDetail) (map[string]any, error) {
	if !input.Valid() {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "课程详情参数无效"}
	}
	page, err := SearchJWXKCourses(ctx, requester, mode, JWXKSearch{Batch: input.Batch, Scope: input.Scope, Page: 1, Size: 50, Keyword: input.Course})
	if err != nil {
		return nil, err
	}
	var teachingClass map[string]any
	for _, course := range page["courses"].([]map[string]any) {
		if Text(course["course_code"]) == input.Course && Text(course["class_id"]) == input.Class {
			teachingClass = course
			break
		}
	}
	if teachingClass == nil {
		return nil, &campus.Error{Code: "JWXK_CLASS_CHANGED", Message: "教学班信息已变化，请刷新课程列表后重试"}
	}
	description := map[string]string{}
	if payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "GET", Path: "/xsxk/web/kc/" + url.PathEscape(input.Course)}); err == nil {
		description = ParseJWXKDetailHTML(string(payload))
		if code := description["course_code"]; code != "" && code != input.Course {
			description = map[string]string{}
		}
	}
	for _, field := range []string{"course_name", "credits", "hours", "department", "course_nature", "course_category", "general_elective_category", "exam_type", "score_scale"} {
		if description[field] != "" {
			teachingClass[field] = description[field]
		}
	}
	if description["course_category"] != "" {
		teachingClass["course_categories"] = uniqueJWXK(append(jwxkStrings(teachingClass["course_categories"]), description["course_category"]))
		teachingClass["normalized_course_category"] = jwxkCategory(description["course_category"])
	}
	programs := []map[string]string{}
	loaded := false
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/ssfa", Form: url.Values{"kch": {input.Course}}}); err == nil {
		if decoded, err := jwxkPayload(payload); err == nil {
			if rows, err := reportRows(decoded["data"]); err == nil {
				_, loaded = decoded["data"]
				seen := map[string]bool{}
				for _, row := range rows {
					program := map[string]string{"grade": Text(row["NJ"]), "training_code": Text(row["trainingCode"]), "program_name": Text(row["FA"]), "module_name": Text(row["parentCode"]), "course_nature": Text(row["KCXZ"]), "course_category": Text(row["KCLB"]), "direction_code": Text(row["ZYFXDM"])}
					identity, _ := json.Marshal(program)
					if program["program_name"] != "" && !seen[string(identity)] {
						seen[string(identity)] = true
						programs = append(programs, program)
					}
				}
			}
		}
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	course := map[string]any{}
	for _, field := range []string{"course_code", "course_name", "credits", "hours", "department", "course_nature", "course_category", "course_categories", "normalized_course_category", "general_elective_category_code", "general_elective_category", "exam_type_code", "exam_type", "score_scale_code", "score_scale"} {
		course[field] = teachingClass[field]
	}
	course["english_name"], course["description"] = description["english_name"], description["description"]
	return map[string]any{"course": course, "teaching_class": teachingClass, "programs": programs, "programs_loaded": loaded}, nil
}
