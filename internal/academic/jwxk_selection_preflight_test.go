package academic

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKSelectionPreflightNeverTrustsClientVerification(t *testing.T) {
	for _, test := range []struct {
		name                                 string
		duplicate, eligible, needsExperiment bool
		wantError                            bool
	}{
		{"valid", false, true, false, false}, {"duplicate", true, true, false, true}, {"unknown eligibility", false, false, false, true}, {"requires experiment", false, true, true, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			checks := 0
			requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
				switch input.Path {
				case "/xsxk/web/now":
					return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
				case "/xsxk/web/studentInfo", "/xsxk/elective/user":
					return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","name":"轮次","typeCode":"02","schoolTerm":"term","canSelect":"1","beginTime":"2020-01-01 00:00:00","endTime":"2030-01-01 00:00:00","clazzTypeList":["XGKC"]}]}}}`), nil
				case "/xsxk/elective/select":
					if test.duplicate {
						return []byte(`{"code":200,"data":[{"KCH":"C1","JXBID":"existing","KRL":10,"YXRS":1}]}`), nil
					}
					return []byte(`{"code":200,"data":[]}`), nil
				case "/xsxk/volunteer/select", "/xsxk/volunteer/xgxk/select", "/xsxk/elective/neu/deselect":
					return []byte(`{"code":200,"data":[]}`), nil
				case "/xsxk/elective/clazz/list":
					var body map[string]any
					json.Unmarshal(input.JSON, &body)
					if body["campus"] != nil || input.ReadOnly {
						t.Fatal("material lookup may reset active batch", input)
					}
					hasTest := "0"
					if test.needsExperiment {
						hasTest = "1"
					}
					return json.Marshal(map[string]any{"code": 200, "data": map[string]any{"rows": []any{map[string]any{"KCH": "C1", "JXBID": "class", "secretVal": "private", "hasTest": hasTest}}}})
				case "/xsxk/elective/check":
					checks++
					if input.Form.Get("secretVal") != "private" || input.BatchID != "round" || input.ReadOnly {
						t.Fatal(input)
					}
					if test.eligible {
						return []byte(`{"code":200,"data":["是否可选：可选"]}`), nil
					}
					return []byte(`{"code":200,"data":["是否可选：未知"]}`), nil
				}
				t.Fatal("preflight sent unexpected request", input.Path)
				return nil, nil
			})
			prepared, err := PrepareJWXKSelection(context.Background(), requester, "direct", JWXKSelect{Batch: "round", Scope: "XGKC", Class: "class", Course: "C1", PreflightVerified: true})
			if (err != nil) != test.wantError {
				t.Fatal(prepared, err)
			}
			if !test.wantError && (checks != 1 || prepared.path != "/xsxk/elective/clazz/add" || prepared.form.Get("clazzType") != "XGKC" || prepared.term != "term") {
				t.Fatal(prepared, checks)
			}
			encoded, _ := json.Marshal(prepared)
			if string(encoded) != "{}" {
				t.Fatal("private preparation can be serialized", string(encoded))
			}
		})
	}
}
