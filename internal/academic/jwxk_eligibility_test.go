package academic

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKEligibilityRequiresExplicitAnswer(t *testing.T) {
	for _, test := range []struct{ line, status string }{
		{"是否可选：可选", "selectable"}, {"是否可选：不可选", "unavailable"}, {"是否可选：未知", "unknown"}, {"是否可选：", "unknown"}, {"是否可选：是", "selectable"}, {"是否可选：否", "unavailable"},
	} {
		result := ParseJWXKEligibility(map[string]any{"code": 200, "data": []any{"学生：private-id", "教学班：private-class", test.line, "先修课程不满足"}}, "class")
		if result["status"] != test.status {
			t.Fatal(result, test)
		}
		encoded, _ := json.Marshal(result)
		if strings.Contains(string(encoded), "private-") {
			t.Fatal("identity leaked")
		}
		if test.status == "selectable" && result["reason"] != "" {
			t.Fatal(result)
		}
	}
	if result := ParseJWXKEligibility(map[string]any{"code": 500, "data": []any{"是否可选：可选"}}, "class"); result["status"] != "unknown" {
		t.Fatal(result)
	}
}

func TestJWXKEligibilityBatchAndDeduplication(t *testing.T) {
	calls := 0
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		calls++
		if calls == 1 {
			if input.Path != "/xsxk/elective/user" {
				t.Fatal(input)
			}
			return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round"}]}}}`), nil
		}
		if input.Path != "/xsxk/elective/check" || input.BatchID != "round" || input.ReadOnly || input.Form.Get("clazzId") != "class" || input.Form.Get("secretVal") != "" {
			t.Fatal(input)
		}
		return []byte(`{"code":200,"data":["是否可选：可选"]}`), nil
	})
	result, err := CheckJWXKEligibility(context.Background(), requester, "direct", JWXKEligibility{Batch: "round", Classes: []string{"class", "class"}})
	if err != nil || calls != 2 || len(result["results"].([]map[string]any)) != 1 {
		t.Fatal(result, err, calls)
	}
	if _, err := CheckJWXKEligibility(context.Background(), requester, "direct", JWXKEligibility{Batch: "round", Classes: []string{"../bad"}}); err == nil || calls != 2 {
		t.Fatal(err, calls)
	}
}
