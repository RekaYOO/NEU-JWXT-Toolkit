package academic

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKConfirmBoundariesAndSingleSend(t *testing.T) {
	for _, test := range []struct {
		name                              string
		acknowledged, eligible, confirmed bool
		response                          string
		writes                            int
		wantError                         bool
		failure                           string
	}{
		{"consent required", false, true, false, `{"code":200}`, 0, true, ""},
		{"account ineligible", true, false, false, `{"code":200}`, 0, true, ""},
		{"already confirmed", true, true, true, `{"code":200}`, 0, false, ""},
		{"confirmed", true, true, false, `{"code":200}`, 1, false, ""},
		{"unknown response", true, true, false, `not-json`, 1, false, "MUTATION_UNCERTAIN"},
		{"risk not automatically confirmed", true, true, false, `{"code":301}`, 1, false, "UNKNOWN"},
		{"queued", true, true, false, `{"code":200,"msg":"正在排队"}`, 1, false, ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			writes, calls := 0, 0
			requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
				calls++
				switch input.Path {
				case "/xsxk/web/now":
					return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
				case "/xsxk/web/studentInfo":
					eligible, confirmed := "0", "0"
					if test.eligible {
						eligible = "1"
					}
					if test.confirmed {
						confirmed = "1"
					}
					return json.Marshal(map[string]any{"code": 200, "data": map[string]any{"student": map[string]any{"electiveBatchList": []any{map[string]any{"code": "round", "name": "轮次", "canSelect": eligible, "needConfirm": "1", "isConfirmed": confirmed, "clazzTypeList": []any{"FANKC"}}}}}})
				case "/xsxk/elective/batch/confirm":
					writes++
					if input.ReadOnly || input.RefreshSession || input.Form.Get("batchId") != "round" || input.Form.Get("isConfirm") != "" {
						t.Fatal(input)
					}
					return []byte(test.response), nil
				}
				t.Fatal(input)
				return nil, nil
			})
			result, err := ConfirmJWXKBatch(context.Background(), requester, "direct", JWXKConfirm{Batch: "round", Acknowledged: test.acknowledged})
			if (err != nil) != test.wantError || writes != test.writes {
				t.Fatal(result, err, writes)
			}
			if !test.acknowledged && calls != 0 {
				t.Fatal("read before consent validation")
			}
			if !test.wantError && result["failure_class"] != test.failure {
				t.Fatal(result)
			}
			if test.name == "queued" && result["queued"] != true {
				t.Fatal(result)
			}
			if test.failure != "" && result["success"] != false {
				t.Fatal("unconfirmed result reported success")
			}
		})
	}
}
