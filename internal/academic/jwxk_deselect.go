package academic

import (
	"context"
	"net/url"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKDeselect struct {
	Batch       string `json:"batch_code"`
	Class       string `json:"class_id"`
	Source      string `json:"selection_source"`
	ConfirmRisk bool   `json:"confirm_risk"`
}

func (input JWXKDeselect) Valid() bool {
	return jwxkBatchCode.MatchString(input.Batch) && jwxkBatchCode.MatchString(input.Class) && (input.Source == "" || input.Source == "yxkcyx" || input.Source == "fakcyx" || input.Source == "xgxkyx")
}

type PreparedJWXKDeselection struct {
	mutation PreparedJWXKSelection
	course   string
}

func (prepared PreparedJWXKDeselection) Term() string   { return prepared.mutation.term }
func (prepared PreparedJWXKDeselection) Course() string { return prepared.course }

func PrepareJWXKDeselection(ctx context.Context, requester ServiceRequester, mode string, input JWXKDeselect) (PreparedJWXKDeselection, error) {
	empty := PreparedJWXKDeselection{}
	if !input.Valid() {
		return empty, selectionPreflightError("退课参数无效")
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
		return empty, selectionPreflightError("当前账号不能在该轮次退课，请核验轮次资格和开放时间")
	}
	if batch["need_confirm"] == true && batch["confirmed"] != true {
		return empty, selectionPreflightError("请先阅读并确认轮次须知")
	}
	selectionMode := Text(batch["selection_type_code"])
	if selectionMode != "02" && selectionMode != "04" {
		return empty, selectionPreflightError("该轮次的退课模式尚未确认")
	}
	if input.Source != "" && (selectionMode == "02" && input.Source != "yxkcyx" || selectionMode == "04" && input.Source == "yxkcyx") {
		return empty, selectionPreflightError("退课来源不属于当前轮次模式")
	}
	activated, err := activateJWXKBatch(ctx, requester, mode, input.Batch, false)
	if err != nil {
		return empty, err
	}
	if Text(activated["typeCode"]) != selectionMode || Text(activated["schoolTerm"]) != Text(batch["term_code"]) {
		return empty, selectionPreflightError("轮次信息已变化，请重新核验")
	}
	for _, feed := range []struct{ source, path, record string }{
		{"yxkcyx", "/xsxk/elective/select", "selected"},
		{"fakcyx", "/xsxk/volunteer/select", "volunteered"},
		{"xgxkyx", "/xsxk/volunteer/xgxk/select", "volunteered"},
	} {
		if input.Source != "" && input.Source != feed.source || selectionMode == "02" && feed.source != "yxkcyx" || selectionMode == "04" && feed.source == "yxkcyx" {
			continue
		}
		payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: feed.path, Form: url.Values{}, PreserveSessionOnAuth: true})
		if err != nil {
			return empty, err
		}
		decoded, err := jwxkPayload(payload)
		if err != nil {
			return empty, err
		}
		if _, ok := decoded["data"].([]any); !ok {
			return empty, selectionPreflightError("学校退课来源数据不完整")
		}
		rows, err := reportRows(decoded["data"])
		if err != nil {
			return empty, err
		}
		for _, row := range rows {
			if firstText(row["JXBID"], row["KXH"]) != input.Class {
				continue
			}
			if Text(row["secretVal"]) == "" || !selectionMutationScope(Text(row["teachingClassType"])) || Text(row["KCH"]) == "" {
				return empty, selectionPreflightError("已选教学班缺少可核验的退课材料")
			}
			row["_official_operation_available"] = true
			course := normalizeJWXKClass(row, map[string]any{})
			ClassifyJWXKRecord(course, selectionMode, input.Batch, Text(batch["term_code"]), feed.record)
			if course["operation_allowed"] != true {
				return empty, selectionPreflightError(Text(course["operation_block_reason"]))
			}
			form := url.Values{"clazzType": {Text(row["teachingClassType"])}, "clazzId": {input.Class}, "secretVal": {Text(row["secretVal"])}, "source": {feed.source}}
			if previous := Text(row["electiveBatchCode"]); previous != "" && previous != input.Batch {
				form.Set("crossBatch", "1")
			}
			return PreparedJWXKDeselection{mutation: PreparedJWXKSelection{path: "/xsxk/elective/neu/clazz/del", form: form, term: Text(batch["term_code"])}, course: Text(row["KCH"])}, nil
		}
	}
	return empty, selectionPreflightError("已选教学班已变化，请刷新官方结果后重试")
}

func SendJWXKDeselection(ctx context.Context, requester ServiceRequester, mode string, prepared PreparedJWXKDeselection, confirmRisk bool) JWXKMutationResult {
	return sendJWXKMutation(ctx, requester, mode, prepared.mutation, confirmRisk)
}

func VerifyJWXKDeselection(ctx context.Context, requester ServiceRequester, mode string, input JWXKDeselect) (bool, string, error) {
	selected, err := FetchJWXKSelected(ctx, requester, mode, input.Batch)
	if err != nil {
		return false, "", err
	}
	term := Text(selected["term_code"])
	selectionMode := Text(selected["selection_type_code"])
	if selectionMode != "02" && selectionMode != "04" {
		return false, term, nil
	}
	if input.Source != "" && (selectionMode == "02" && input.Source != "yxkcyx" || selectionMode == "04" && input.Source == "yxkcyx") {
		return false, term, nil
	}
	feed := "selected"
	if selectionMode == "04" {
		feed = "volunteered"
	}
	for _, failure := range selected["unavailable_feeds"].([]string) {
		if failure == feed || selectionMode == "04" && failure == "general_volunteered" || input.Source == "" && failure != "withdrawal" {
			return false, term, nil
		}
	}
	for _, resultFeed := range []string{"selected", "volunteered"} {
		for _, course := range selected[resultFeed].([]map[string]any) {
			if Text(course["class_id"]) == input.Class {
				return false, term, nil
			}
		}
	}
	return true, term, nil
}
