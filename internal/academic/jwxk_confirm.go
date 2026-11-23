package academic

import (
	"context"
	"net/url"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKConfirm struct {
	Batch        string `json:"batch_code"`
	Acknowledged bool   `json:"acknowledged"`
}

func (input JWXKConfirm) Valid() bool {
	return input.Acknowledged && jwxkBatchCode.MatchString(input.Batch)
}

func ConfirmJWXKBatch(ctx context.Context, requester ServiceRequester, mode string, input JWXKConfirm) (map[string]any, error) {
	if !input.Valid() {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "请先阅读并明确确认轮次须知"}
	}
	contextData, err := FetchJWXKContext(ctx, requester, mode)
	if err != nil {
		return nil, err
	}
	var batch map[string]any
	for _, candidate := range contextData["batches"].([]map[string]any) {
		if Text(candidate["code"]) == input.Batch {
			batch = candidate
			break
		}
	}
	if batch == nil || batch["account_selectable"] != true {
		return nil, &campus.Error{Code: "JWXK_BATCH_INELIGIBLE", Message: "当前账号不能确认该选课轮次"}
	}
	result := map[string]any{"success": true, "queued": false, "requires_confirmation": false, "code": "200", "message": "轮次须知已确认", "failure_class": ""}
	if batch["need_confirm"] != true || batch["confirmed"] == true {
		return result, nil
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/batch/confirm", Form: url.Values{"batchId": {input.Batch}}})
	if err != nil {
		return nil, err
	}
	decoded, err := Decode(payload)
	if err != nil || Text(decoded["code"]) == "" {
		return map[string]any{"success": false, "queued": false, "requires_confirmation": false, "code": "unknown", "message": "学校未返回明确确认结果，请重新核验轮次状态", "failure_class": "MUTATION_UNCERTAIN"}, nil
	}
	code := Text(decoded["code"])
	message := firstText(decoded["msg"], decoded["message"])
	if message == "" {
		if code == "200" {
			message = "轮次须知已确认"
		} else {
			message = "学校未确认轮次须知，请核验轮次状态"
		}
	}
	queued := false
	for _, marker := range []string{"队列", "排队", "处理中", "等待处理", "等待确认"} {
		queued = queued || code == "200" && strings.Contains(message, marker)
	}
	result["success"], result["queued"], result["requires_confirmation"], result["code"], result["message"] = code == "200", queued, code == "301", code, message
	if code != "200" {
		result["failure_class"] = "UNKNOWN"
	}
	return result, nil
}
