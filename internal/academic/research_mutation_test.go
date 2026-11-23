package academic

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
)

func TestResearchEnrollmentUsesOfficialEligibilityAndSingleSend(t *testing.T) {
	input := ResearchEnrollment{Topic: "topic", Batch: "batch", Phone: "123456", Email: "example@test.invalid", Reason: "申请"}
	for _, scenario := range []string{"confirmed", "rejected", "lost", "missing-code", "bad-code"} {
		t.Run(scenario, func(t *testing.T) {
			calls := 0
			requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
				calls++
				if method != "POST" || !strings.HasSuffix(target, "/api/kyxl/ktbm/save.do") || form.Get("KTWID") != "topic" {
					t.Fatal(target, form)
				}
				var payload map[string]string
				if json.Unmarshal([]byte(form.Get("FORMJSON")), &payload) != nil || payload["PJJD"] != "3.5" || payload["PM"] != "25.93" || payload["SQLY"] != "申请" {
					t.Fatal(payload)
				}
				switch scenario {
				case "lost":
					return nil, errors.New("synthetic disconnect")
				case "rejected":
					return []byte(`{"code":"1"}`), nil
				case "missing-code":
					return []byte(`{}`), nil
				case "bad-code":
					return []byte(`{"code":false}`), nil
				}
				return []byte(`{"code":"0"}`), nil
			})
			state, _ := SendResearchEnrollment(context.Background(), requester, input, map[string]any{"available": true, "gpa": "3.5", "major_rank": "25.93"})
			expected := scenario
			if scenario == "lost" || scenario == "missing-code" || scenario == "bad-code" {
				expected = "unknown"
			}
			if state != expected || calls != 1 {
				t.Fatal(state, calls)
			}
		})
	}
}

func TestResearchMutationRejectsMissingEligibilityAndUsesOfficialCancellationRule(t *testing.T) {
	calls := 0
	requester := requestFunc(func(_ context.Context, _, target string, form url.Values) ([]byte, error) {
		calls++
		if !strings.HasSuffix(target, "/qxbm.do") || form.Get("querySetting") != researchRule("WID", "topic") {
			t.Fatal("wrong cancellation identity", target, form)
		}
		return []byte(`{"code":0}`), nil
	})
	input := ResearchEnrollment{Topic: "topic", Batch: "batch", Phone: "123456", Email: "example@test.invalid"}
	for _, eligibility := range []map[string]any{{"available": false}, {"available": true, "gpa": "", "major_rank": "25"}} {
		if state, _ := SendResearchEnrollment(context.Background(), requester, input, eligibility); state != "not_sent" || calls != 0 {
			t.Fatal(state, calls)
		}
	}
	if state, _ := SendResearchCancellation(context.Background(), requester, "topic"); state != "confirmed" || calls != 1 {
		t.Fatal(state, calls)
	}
}
