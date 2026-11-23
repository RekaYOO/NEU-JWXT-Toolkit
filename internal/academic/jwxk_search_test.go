package academic

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKSearchReactivatesBatchBeforeSingleRetry(t *testing.T) {
	calls := []string{}
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		calls = append(calls, input.Path)
		if input.Path == "/xsxk/elective/user" {
			if input.RefreshSession != (len(calls) == 3) || input.Form.Get("batchId") != "round" {
				t.Fatal(input)
			}
			return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","typeCode":"04"}]}}}`), nil
		}
		if input.ReadOnly || input.BatchID != "round" {
			t.Fatal("query must not use generic auth replay", input)
		}
		var body map[string]any
		if err := json.Unmarshal(input.JSON, &body); err != nil {
			t.Fatal(err)
		}
		if _, exists := body["campus"]; exists || body["KEY"] != "测试" {
			t.Fatal(body)
		}
		if len(calls) == 2 {
			return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED"}
		}
		return []byte(`{"code":200,"data":{"total":1,"rows":[{"KCH":"C1","KCM":"测试","tcList":[{"JXBID":"A","KRL":10,"YXRS":3,"QZXKRS":20},{"JXBID":"B"}]}]}}`), nil
	})
	result, err := SearchJWXKCourses(context.Background(), requester, "direct", JWXKSearch{Batch: "round", Scope: "ALLKC", Page: 1, Size: 20, Campus: "01", Keyword: " 测试 "})
	if err != nil || len(calls) != 4 {
		t.Fatal(result, err, calls)
	}
	courses := result["courses"].([]map[string]any)
	if result["total"] != 1 || len(courses) != 2 || courses[0]["capacity"] != 7 || courses[0]["full"] != false {
		t.Fatal(result)
	}
}

func TestJWXKSearchRejectsIncompletePageAndUnboundedRetry(t *testing.T) {
	for _, response := range []string{"auth", `{"code":200,"data":{"total":1,"rows":[]}}`, `{"code":200,"data":{"total":0,"rows":{}}}`} {
		t.Run(response, func(t *testing.T) {
			calls := 0
			requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
				calls++
				if input.Path == "/xsxk/elective/user" {
					return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round"}]}}}`), nil
				}
				if response == "auth" {
					return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED"}
				}
				return []byte(response), nil
			})
			if _, err := SearchJWXKCourses(context.Background(), requester, "direct", JWXKSearch{Batch: "round", Scope: "FANKC", Page: 1, Size: 20}); err == nil || calls > 4 {
				t.Fatal(err, calls)
			}
		})
	}
}

func TestJWXKCourseProjectionAndScheduleCompatibility(t *testing.T) {
	courses, err := NormalizeJWXKCourses([]any{map[string]any{"KCH": "C1", "XKSM": "须知", "secretVal": "never expose", "SYXS": "NaN", "YPSJDD": "1-2[周]/星期一/第一节-第二节/教师/教室", "tcList": []any{map[string]any{"JXBID": "A"}}}})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(courses)
	if err != nil || strings.Contains(string(encoded), "never expose") || courses[0]["notice"] != "须知" || courses[0]["experiment_hours"] != nil {
		t.Fatal(string(encoded), err)
	}
	meetings := courses[0]["schedules"].([]map[string]any)
	if len(meetings) != 1 || !reflect.DeepEqual(meetings[0]["weeks"], []int{1, 2}) || len(Text(meetings[0]["meeting_id"])) != len("jwxk-mtg-")+20 {
		t.Fatal(meetings)
	}
	for _, value := range []string{"NaN", "+Inf", "-Inf"} {
		if jwxkDecimal(value) != nil {
			t.Fatal(value)
		}
	}
}
