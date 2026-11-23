package academic

import (
	"context"
	"net/url"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKEligibility struct {
	Batch   string   `json:"batch_code"`
	Classes []string `json:"class_ids"`
}

func (input JWXKEligibility) Valid() bool {
	if !jwxkBatchCode.MatchString(input.Batch) || len(input.Classes) < 1 || len(input.Classes) > 50 {
		return false
	}
	for _, class := range input.Classes {
		if !jwxkBatchCode.MatchString(class) {
			return false
		}
	}
	return true
}

func ParseJWXKEligibility(payload map[string]any, class string) map[string]any {
	status := "unknown"
	reasons := []string{}
	lines, _ := payload["data"].([]any)
	for _, value := range lines {
		line, ok := value.(string)
		if !ok {
			continue
		}
		line = strings.TrimSpace(line)
		compact := strings.Join(strings.Fields(line), "")
		if position := strings.Index(compact, "是否可选"); position >= 0 && status == "unknown" {
			answer := strings.TrimLeft(compact[position+len("是否可选"):], "：:")
			if answer == "不可选" || answer == "否" {
				status = "unavailable"
			} else if answer == "可选" || answer == "是" {
				status = "selectable"
			}
		}
		ignored := line == ""
		for _, prefix := range []string{"是否可选", "学生", "教学班", "选课轮次"} {
			ignored = ignored || strings.HasPrefix(compact, prefix)
		}
		if !ignored {
			reasons = append(reasons, line)
		}
	}
	if Text(payload["code"]) != "" && Text(payload["code"]) != "200" {
		status = "unknown"
	}
	reason := ""
	if status != "selectable" {
		fallback := "官方暂未返回明确的可选结果"
		if status == "unavailable" {
			fallback = "当前轮次不可选择该教学班"
		}
		reason = firstText(strings.Join(reasons, "；"), fallback)
	}
	return map[string]any{"class_id": class, "status": status, "reason": reason}
}

func CheckJWXKEligibility(ctx context.Context, requester ServiceRequester, mode string, input JWXKEligibility) (map[string]any, error) {
	if !input.Valid() {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "课程资格查询参数无效"}
	}
	if _, err := activateJWXKBatch(ctx, requester, mode, input.Batch, false); err != nil {
		return nil, err
	}
	results := []map[string]any{}
	for _, class := range uniqueJWXK(input.Classes) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/check", BatchID: input.Batch, Form: url.Values{"clazzId": {class}, "secretVal": {""}}})
		if err != nil {
			return nil, err
		}
		decoded, err := Decode(payload)
		if err != nil {
			return nil, err
		}
		results = append(results, ParseJWXKEligibility(decoded, class))
	}
	return map[string]any{"results": results}, nil
}
