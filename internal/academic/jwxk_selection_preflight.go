package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKSelect struct {
	Batch             string `json:"batch_code"`
	Scope             string `json:"teaching_class_type"`
	Class             string `json:"class_id"`
	Course            string `json:"course_code"`
	Weight            *int   `json:"weight"`
	ConfirmRisk       bool   `json:"confirm_risk"`
	PreflightVerified bool   `json:"preflight_verified"`
}

func (input JWXKSelect) Valid() bool {
	return (JWXKDetail{Batch: input.Batch, Scope: input.Scope, Class: input.Class, Course: input.Course}).Valid() && selectionMutationScope(input.Scope) && (input.Weight == nil || *input.Weight >= 5 && *input.Weight <= 150)
}

type PreparedJWXKSelection struct {
	path string
	form url.Values
	term string
}

func (prepared PreparedJWXKSelection) Term() string { return prepared.term }

func selectionPreflightError(message string) error {
	return &campus.Error{Code: "JWXK_PREFLIGHT_FAILED", Message: message}
}

func selectionMutationScope(scope string) bool {
	return jwxkScopeCode.MatchString(scope) && scope != "ALL" && scope != "ROUND" && scope != "ALLKC"
}

func findJWXKSelectionMaterial(ctx context.Context, requester ServiceRequester, mode string, input JWXKSelect, menus []map[string]string) (map[string]any, string, error) {
	scopes := []string{input.Scope}
	for _, menu := range menus {
		if selectionMutationScope(menu["code"]) {
			scopes = append(scopes, menu["code"])
		}
	}
	for _, scope := range uniqueJWXK(scopes) {
		if _, err := activateJWXKBatch(ctx, requester, mode, input.Batch, false); err != nil {
			return nil, "", err
		}
		body, _ := json.Marshal(map[string]any{"teachingClassType": scope, "pageNumber": 1, "pageSize": 50, "orderBy": "", "KEY": input.Course})
		payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/clazz/list", JSON: body, BatchID: input.Batch})
		if err != nil {
			return nil, "", err
		}
		decoded, err := jwxkPayload(payload)
		if err != nil {
			return nil, "", err
		}
		data := Object(decoded["data"])
		if _, ok := data["rows"].([]any); !ok {
			return nil, "", selectionPreflightError("学校未返回有效的教学班列表")
		}
		rows, err := reportRows(data["rows"])
		if err != nil {
			return nil, "", err
		}
		for _, parent := range rows {
			children, err := reportRows(parent["tcList"])
			if err != nil {
				return nil, "", err
			}
			if len(children) == 0 {
				children = []map[string]any{parent}
			}
			for _, child := range children {
				row := map[string]any{}
				for key, value := range parent {
					row[key] = value
				}
				for key, value := range child {
					row[key] = value
				}
				if firstText(row["JXBID"], row["KXH"]) != input.Class || Text(row["KCH"]) != input.Course || Text(row["secretVal"]) == "" {
					continue
				}
				mutationScope := firstText(row["teachingClassType"], row["clazzType"])
				if !selectionMutationScope(mutationScope) && selectionMutationScope(scope) {
					mutationScope = scope
				}
				if selectionMutationScope(mutationScope) {
					return row, mutationScope, nil
				}
			}
		}
	}
	return nil, "", selectionPreflightError("无法确认教学班的真实投选类型或操作材料，请刷新目录")
}

func PrepareJWXKSelection(ctx context.Context, requester ServiceRequester, mode string, input JWXKSelect) (PreparedJWXKSelection, error) {
	empty := PreparedJWXKSelection{}
	if !input.Valid() {
		return empty, selectionPreflightError("选课参数无效")
	}
	contextData, err := FetchJWXKContext(ctx, requester, mode)
	if err != nil {
		return empty, err
	}
	var batch map[string]any
	for _, candidate := range contextData["batches"].([]map[string]any) {
		if Text(candidate["code"]) == input.Batch {
			batch = candidate
			break
		}
	}
	if batch == nil || batch["account_selectable"] != true || batch["state"] != "active" {
		return empty, selectionPreflightError("当前账号不能在该轮次提交，请核验轮次资格和开放时间")
	}
	if batch["need_confirm"] == true && batch["confirmed"] != true {
		return empty, selectionPreflightError("请先阅读并确认轮次须知")
	}
	selectionMode := Text(batch["selection_type_code"])
	if selectionMode != "02" && selectionMode != "04" {
		return empty, selectionPreflightError("该轮次的提交模式尚未确认")
	}
	if selectionMode == "04" {
		if input.Weight == nil {
			return empty, selectionPreflightError("权重轮次必须填写投注权重")
		}
		budget, err := FetchJWXKWeightBudget(ctx, requester, mode, input.Batch)
		if err != nil {
			return empty, err
		}
		if *input.Weight < int(Number(budget["minimum"])) || *input.Weight%int(Number(budget["step"])) != 0 || *input.Weight > int(Number(budget["remaining"])) {
			return empty, selectionPreflightError("投入权重不符合学校最低值、步长或余额要求")
		}
	} else if input.Weight != nil {
		return empty, selectionPreflightError("抢选轮次不接受权重参数")
	}
	selected, err := FetchJWXKSelected(ctx, requester, mode, input.Batch)
	if err != nil {
		return empty, err
	}
	if Text(selected["selection_type_code"]) != selectionMode || Text(selected["term_code"]) != Text(batch["term_code"]) {
		return empty, selectionPreflightError("轮次类型或学期已变化，请刷新后重新核验")
	}
	feed := "selected"
	if selectionMode == "04" {
		feed = "volunteered"
	}
	for _, failed := range selected["unavailable_feeds"].([]string) {
		if failed == feed || selectionMode == "04" && failed == "general_volunteered" {
			return empty, selectionPreflightError("当前轮次已选结果未完整读取，暂时不能核验重复课程")
		}
	}
	for _, course := range selected[feed].([]map[string]any) {
		if course["current_batch_record"] == true && strings.EqualFold(Text(course["course_code"]), input.Course) {
			return empty, selectionPreflightError("当前已选结果中存在同课程代码，不能重复选择")
		}
	}
	row, scope, err := findJWXKSelectionMaterial(ctx, requester, mode, input, batch["menus"].([]map[string]string))
	if err != nil {
		return empty, err
	}
	if Text(row["hasTest"]) == "1" {
		return empty, selectionPreflightError("该课程要求同时选择实验班，请使用实验班选择流程")
	}
	payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/check", BatchID: input.Batch, Form: url.Values{"clazzId": {input.Class}, "secretVal": {Text(row["secretVal"])}}})
	if err != nil {
		return empty, err
	}
	decoded, err := Decode(payload)
	if err != nil {
		return empty, err
	}
	eligibility := ParseJWXKEligibility(decoded, input.Class)
	if eligibility["status"] != "selectable" {
		return empty, selectionPreflightError(firstText(eligibility["reason"], "学校未明确确认该教学班可选"))
	}
	form := url.Values{"clazzType": {scope}, "clazzId": {input.Class}, "secretVal": {Text(row["secretVal"])}, "batchId": {input.Batch}, "needBook": {""}}
	path := "/xsxk/elective/clazz/add"
	if selectionMode == "04" {
		form.Set("weight", Text(*input.Weight))
		path = "/xsxk/elective/neu/clazz/weightAdd"
	}
	return PreparedJWXKSelection{path: path, form: form, term: Text(batch["term_code"])}, nil
}
