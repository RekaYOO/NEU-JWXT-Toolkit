package academic

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

type ExperimentMutation struct {
	Term    string `json:"term"`
	Task    string `json:"task_id"`
	Project string `json:"project_code"`
	Round   string `json:"round_id"`
}

func (input ExperimentMutation) Valid() bool {
	return timetableTermPattern.MatchString(input.Term) && ValidEvaluationID(input.Task) && ValidEvaluationID(input.Project) && ValidEvaluationID(input.Round)
}

func ExperimentProjectState(payload map[string]any, input ExperimentMutation) (string, string, bool) {
	courses, _ := payload["courses"].([]map[string]any)
	for _, course := range courses {
		if Text(course["task_id"]) != input.Task || Text(course["term_code"]) != input.Term {
			continue
		}
		projects, _ := course["projects"].([]map[string]any)
		for _, project := range projects {
			if Text(project["project_code"]) == input.Project {
				return Text(course["course_no"]), Text(project["selected_round_id"]), true
			}
		}
	}
	return "", "", false
}

func ExperimentMutationObserved(payload map[string]any, input ExperimentMutation, action string) bool {
	_, selected, found := ExperimentProjectState(payload, input)
	results, ok := payload["selected_results"].([]map[string]any)
	if !ok || Text(payload["term"]) != input.Term {
		return false
	}
	officialSelected := false
	for _, result := range results {
		if Text(result["task_id"]) != input.Task || Text(result["project_code"]) != input.Project || Text(result["selected_round_id"]) != input.Round {
			continue
		}
		status := Text(result["select_status"])
		if strings.Contains(status, "退") || strings.Contains(status, "取消") {
			continue
		}
		officialSelected = true
	}
	if action == "select" {
		return found && selected == input.Round || officialSelected
	}
	return action == "deselect" && (!found || selected != input.Round) && !officialSelected
}

func SendExperimentMutation(ctx context.Context, requester Requester, input ExperimentMutation, action string) (string, string) {
	if !input.Valid() || action != "select" && action != "deselect" {
		return "not_sent", "选退课参数无效，未发送请求"
	}
	operation := "selectedMustDoProjectRound"
	if action == "deselect" {
		operation = "deselectedMustDoProjectRound"
	}
	encoded, err := requester.Request(ctx, http.MethodPost, experimentBase+"xsxk/"+operation+".do", url.Values{"XNXQDM": {input.Term}, "SYRW_WID": {input.Task}, "SYXMDM": {input.Project}, "PKLC_WID": {input.Round}})
	if err != nil {
		return "unknown", "学校操作结果尚未确认，请核验结果；不会重复提交"
	}
	payload, err := Decode(encoded)
	if err != nil || payload["code"] == nil {
		return "unknown", "学校响应未能确认操作结果，请核验结果；不会重复提交"
	}
	if _, err := strconv.ParseInt(Text(payload["code"]), 10, 64); err != nil {
		return "unknown", "学校响应未能确认操作结果，请核验结果；不会重复提交"
	}
	if Text(payload["code"]) != "0" {
		return "rejected", "教务系统未接受此次操作，请刷新班次和选课状态后重试"
	}
	return "confirmed", "教务系统已确认操作成功"
}
