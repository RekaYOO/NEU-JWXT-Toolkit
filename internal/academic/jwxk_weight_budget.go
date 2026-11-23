package academic

import (
	"context"
	"net/url"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func jwxkWeightNumber(value any) (int, bool) {
	number, err := strconv.Atoi(Text(value))
	return number, err == nil && number >= 0 && number <= 1000000000
}

func jwxkNamedWeight(value any, names ...string) (int, bool, error) {
	accepted := map[string]bool{}
	for _, name := range names {
		accepted[strings.ToLower(name)] = true
	}
	number, found := 0, false
	var walk func(any, int) error
	walk = func(value any, depth int) error {
		if depth > 32 {
			return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校权重数据嵌套过深"}
		}
		switch object := value.(type) {
		case map[string]any:
			for key, child := range object {
				if key == "electiveBatchList" || key == "expElectiveBatchList" || key == "termWeightList" {
					continue
				}
				if accepted[strings.ToLower(key)] {
					parsed, valid := jwxkWeightNumber(child)
					if !valid {
						return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校权重数值无效"}
					}
					if found && number != parsed {
						return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校返回互相矛盾的权重数值，请重新核验"}
					}
					number, found = parsed, true
				} else if err := walk(child, depth+1); err != nil {
					return err
				}
			}
		case []any:
			for _, child := range object {
				if err := walk(child, depth+1); err != nil {
					return err
				}
			}
		}
		return nil
	}
	err := walk(value, 0)
	return number, found, err
}

func FetchJWXKWeightBudget(ctx context.Context, requester ServiceRequester, mode, batchCode string) (map[string]any, error) {
	if !jwxkBatchCode.MatchString(batchCode) {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "轮次参数无效"}
	}
	payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: "/xsxk/elective/user", Form: url.Values{"batchId": {batchCode}}, ReadOnly: true})
	if err != nil {
		return nil, err
	}
	decoded, err := jwxkPayload(payload)
	if err != nil {
		return nil, err
	}
	data := Object(decoded["data"])
	student := Object(data["student"])
	var batch map[string]any
	for _, field := range []string{"electiveBatchList", "expElectiveBatchList"} {
		rows, err := reportRows(student[field])
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			if firstText(row["code"], row["batchCode"], row["id"]) == batchCode {
				batch = row
				break
			}
		}
	}
	if batch == nil {
		return nil, &campus.Error{Code: "JWXK_BATCH_MISSING", Message: "该轮次不属于当前账号"}
	}
	term := firstText(batch["termCode"], batch["schoolTerm"], batch["semester"], batch["xnxq"])
	remaining, found := 0, false
	weights, err := reportRows(student["termWeightList"])
	if err != nil {
		return nil, err
	}
	for _, weight := range weights {
		if term == "" || Text(weight["termCode"]) != term {
			continue
		}
		parsed, valid := jwxkWeightNumber(weight["weight"])
		if !valid || found && parsed != remaining {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校学期权重数据无效或不一致"}
		}
		remaining, found = parsed, true
	}
	if !found {
		remaining, found, err = jwxkNamedWeight(map[string]any{"context": data, "batch": batch}, "remainingWeight", "remainWeight", "availableWeight", "weightBalance", "SYQZ", "KYSYQZ", "QZYE", "surplusWeight")
		if err != nil {
			return nil, err
		}
	}
	if !found {
		return nil, &campus.Error{Code: "JWXK_WEIGHT_UNAVAILABLE", Message: "学校尚未返回可核验的剩余权重，请刷新轮次后重试"}
	}
	source := map[string]any{"context": data, "batch": batch}
	total, totalFound, err := jwxkNamedWeight(source, "totalWeight", "weightTotal", "QZZE", "ZQZ")
	if err != nil {
		return nil, err
	}
	if !totalFound {
		total = remaining
	}
	if total < remaining {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校总权重小于剩余权重，请重新核验"}
	}
	minimum, minimumFound, err := jwxkNamedWeight(source, "minimumWeight", "minWeight", "minimumBid", "minBid", "ZXQZ")
	if err != nil {
		return nil, err
	}
	if !minimumFound || minimum == 0 {
		minimum = 5
	}
	step, stepFound, err := jwxkNamedWeight(source, "weightStep", "bidStep", "QZBC")
	if err != nil {
		return nil, err
	}
	if !stepFound || step == 0 {
		step = 1
	}
	return map[string]any{"remaining": remaining, "total": total, "used": total - remaining, "minimum": minimum, "step": step, "source": "official_round_context", "usage_known": totalFound}, nil
}
