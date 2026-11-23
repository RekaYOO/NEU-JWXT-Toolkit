package academic

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKSearch struct {
	Batch   string            `json:"batch_code"`
	Scope   string            `json:"teaching_class_type"`
	Page    int               `json:"page_number"`
	Size    int               `json:"page_size"`
	Keyword string            `json:"keyword"`
	Campus  string            `json:"campus"`
	Order   string            `json:"order_by"`
	Filters map[string]string `json:"filters"`
}

var jwxkScopeCode = regexp.MustCompile(`^[A-Z0-9_]{1,24}$`)
var jwxkOrder = regexp.MustCompile(`^[+-]?[A-Z][A-Z0-9_]{0,31}$`)
var jwxkFilterFields = map[string]bool{"SFCT": true, "SFYM": true, "SFYX": true, "KCXZ": true, "KCLB": true, "KKDW": true, "XGXKLB": true, "XGXKLBDM": true, "CXCKLX": true, "SKXQ": true, "KSJC": true, "JSJC": true, "ZXNJ": true, "ZXYX": true, "ZXZY": true, "FXNJ": true, "FXYX": true, "FXZY": true, "TJBJ": true}

func (input JWXKSearch) Valid() bool {
	if !jwxkBatchCode.MatchString(input.Batch) || !jwxkScopeCode.MatchString(input.Scope) || input.Scope == "ALL" || input.Scope == "ROUND" || input.Page < 1 || input.Page > 1000 || input.Size < 1 || input.Size > 50 || utf8.RuneCountInString(input.Keyword) > 100 || utf8.RuneCountInString(input.Campus) > 40 || len(input.Filters) > 16 || (input.Order != "" && !jwxkOrder.MatchString(input.Order)) {
		return false
	}
	for key, value := range input.Filters {
		if !jwxkFilterFields[key] || utf8.RuneCountInString(value) > 100 {
			return false
		}
	}
	return true
}

func jwxkPayload(payload []byte) (map[string]any, error) {
	data, err := Decode(payload)
	if err != nil {
		return nil, err
	}
	if Text(data["code"]) != "200" {
		return nil, &campus.Error{Code: "JWXK_BUSINESS_ERROR", Message: "学校选课系统拒绝该查询，请检查轮次和课程范围"}
	}
	return data, nil
}

func activateJWXKBatch(ctx context.Context, requester ServiceRequester, mode, batch string, refresh bool) (map[string]any, error) {
	payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/user", Form: url.Values{"batchId": {batch}}, ReadOnly: true, RefreshSession: refresh})
	if err != nil {
		return nil, err
	}
	decoded, err := jwxkPayload(payload)
	if err != nil {
		return nil, err
	}
	student := Object(Object(decoded["data"])["student"])
	if student == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校没有返回当前轮次账号资料"}
	}
	for _, field := range []string{"electiveBatchList", "expElectiveBatchList"} {
		rows, err := reportRows(student[field])
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			if firstText(row["code"], row["batchCode"], row["id"]) == batch {
				return row, nil
			}
		}
	}
	return nil, &campus.Error{Code: "JWXK_BATCH_MISSING", Message: "该选课轮次不存在或当前账号不可见"}
}

func SearchJWXKCourses(ctx context.Context, requester ServiceRequester, mode string, input JWXKSearch) (map[string]any, error) {
	if !input.Valid() {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "选课查询参数无效"}
	}
	batch, err := activateJWXKBatch(ctx, requester, mode, input.Batch, false)
	if err != nil {
		return nil, err
	}
	body := map[string]any{"teachingClassType": input.Scope, "pageNumber": input.Page, "pageSize": input.Size, "orderBy": input.Order}
	if input.Scope != "ALLKC" && input.Campus != "" {
		body["campus"] = jwxkCampus(input.Campus)
	}
	if keyword := strings.TrimSpace(input.Keyword); keyword != "" {
		body["KEY"] = keyword
	}
	for key, value := range input.Filters {
		if value != "" {
			body[key] = value
		}
	}
	encoded, _ := json.Marshal(body)
	for attempt := 0; attempt < 2; attempt++ {
		payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/clazz/list", JSON: encoded, BatchID: input.Batch, ReadOnly: false})
		if err != nil {
			var failure *campus.Error
			if attempt == 0 && errors.As(err, &failure) && failure.Code == "SERVICE_AUTH_REQUIRED" {
				batch, err = activateJWXKBatch(ctx, requester, mode, input.Batch, true)
				if err != nil {
					return nil, err
				}
				continue
			}
			return nil, err
		}
		decoded, err := jwxkPayload(payload)
		if err != nil {
			return nil, err
		}
		data := Object(decoded["data"])
		if data == nil {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校课程分页数据缺失"}
		}
		total, err := strconv.Atoi(Text(data["total"]))
		if err != nil || total < 0 {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校课程总数无效"}
		}
		rows, ok := data["rows"].([]any)
		if !ok || len(rows) > input.Size || len(rows) > total || len(rows) == 0 && (input.Page-1)*input.Size < total {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校课程分页响应不完整"}
		}
		courses, err := NormalizeJWXKCourses(rows)
		if err != nil {
			return nil, err
		}
		ApplyJWXKMarket(courses, firstText(batch["typeCode"], batch["selectionTypeCode"]))
		return map[string]any{"total": total, "row_count": len(rows), "courses": courses}, nil
	}
	return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED", Message: "选课查询会话失效，请恢复连接"}
}
