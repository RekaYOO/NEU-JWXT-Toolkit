package academic

import (
	"context"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKDeselectionVerificationRequiresCompleteRelevantResults(t *testing.T) {
	for _, scenario := range []string{"missing-feed", "other-feed-still-present", "absent"} {
		requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
			if input.Path == "/xsxk/elective/user" {
				return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","typeCode":"02","schoolTerm":"term"}]}}}`), nil
			}
			if input.Path == "/xsxk/elective/select" && scenario == "missing-feed" {
				return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED"}
			}
			if input.Path == "/xsxk/volunteer/select" && scenario == "other-feed-still-present" {
				return []byte(`{"code":200,"data":[{"JXBID":"class","KCH":"C1"}]}`), nil
			}
			return []byte(`{"code":200,"data":[]}`), nil
		})
		verified, term, err := VerifyJWXKDeselection(context.Background(), requester, "direct", JWXKDeselect{Batch: "round", Class: "class", Source: "yxkcyx"})
		if err != nil || term != "term" || verified != (scenario == "absent") {
			t.Fatal(scenario, verified, err)
		}
	}
}
