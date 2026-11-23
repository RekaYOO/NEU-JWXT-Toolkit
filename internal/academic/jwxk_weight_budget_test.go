package academic

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKWeightBudgetUsesMatchingTermAndSingleRequest(t *testing.T) {
	calls := 0
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		calls++
		if input.Path != "/xsxk/elective/user" || input.Form.Get("batchId") != "round" || !input.ReadOnly {
			t.Fatal(input)
		}
		return []byte(`{"code":200,"data":{"totalWeight":150,"student":{"name":"private-name","electiveBatchList":[{"code":"round","schoolTerm":"current"},{"code":"old-round","remainingWeight":999}],"termWeightList":[{"termCode":"old","weight":999},{"termCode":"current","weight":75}]}}}`), nil
	})
	result, err := FetchJWXKWeightBudget(context.Background(), requester, "direct", "round")
	if err != nil || calls != 1 || result["remaining"] != 75 || result["used"] != 75 || result["minimum"] != 5 || result["step"] != 1 || result["usage_known"] != true {
		t.Fatal(result, err, calls)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "private-name") || strings.Contains(string(encoded), "old-round") {
		t.Fatal("private context exposed")
	}
}

func TestJWXKWeightBudgetRejectsAmbiguousAndMissingAmounts(t *testing.T) {
	for _, extra := range []string{
		`"totalWeight":150`,
		`"remainingWeight":-1`,
		`"remainingWeight":1.5`,
		`"remainingWeight":20,"remainWeight":30`,
		`"remainingWeight":100,"totalWeight":50`,
	} {
		requester := serviceRequestFunc(func(context.Context, campus.ServiceRequest) ([]byte, error) {
			return []byte(`{"code":200,"data":{` + extra + `,"student":{"electiveBatchList":[{"code":"round"}]}}}`), nil
		})
		if result, err := FetchJWXKWeightBudget(context.Background(), requester, "direct", "round"); err == nil || result != nil {
			t.Fatal(extra, result, err)
		}
	}
}

func TestJWXKWeightBudgetZeroIsValidAndMissingUsageMarked(t *testing.T) {
	requester := serviceRequestFunc(func(context.Context, campus.ServiceRequest) ([]byte, error) {
		return []byte(`{"code":200,"data":{"availableWeight":0,"minimumBid":10,"weightStep":5,"student":{"electiveBatchList":[{"code":"round"}]}}}`), nil
	})
	result, err := FetchJWXKWeightBudget(context.Background(), requester, "direct", "round")
	if err != nil || result["remaining"] != 0 || result["usage_known"] != false || result["minimum"] != 10 || result["step"] != 5 {
		t.Fatal(result, err)
	}
}
