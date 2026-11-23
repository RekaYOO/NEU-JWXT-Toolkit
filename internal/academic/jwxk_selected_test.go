package academic

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKSelectedPartialFeedsAndSecretProjection(t *testing.T) {
	calls := 0
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		calls++
		if input.Path == "/xsxk/elective/user" {
			return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","typeCode":"04","schoolTerm":"term"}]}}}`), nil
		}
		if input.ReadOnly || !input.PreserveSessionOnAuth {
			t.Fatal("optional feed can reset active batch", input)
		}
		if input.Path == "/xsxk/elective/select" {
			return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED"}
		}
		if input.Path == "/xsxk/volunteer/select" {
			return []byte(`{"code":200,"data":[{"JXBID":"class","QZXKRS":2,"KRL":10,"batchCode":"old","secretVal":"sensitive-value"}]}`), nil
		}
		return []byte(`{"code":200,"data":[]}`), nil
	})
	result, err := FetchJWXKSelected(context.Background(), requester, "direct", "round")
	if err != nil || calls != 5 || result["complete"] != false {
		t.Fatal(result, err, calls)
	}
	course := result["volunteered"].([]map[string]any)[0]
	if course["operation_allowed"] != true || course["selection_source"] != "fakcyx" {
		t.Fatal(course)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "sensitive-value") || strings.Contains(string(encoded), "secretVal") {
		t.Fatal("operation secret exposed")
	}
}

func TestJWXKRecordClassificationPrecedence(t *testing.T) {
	for _, test := range []struct {
		name, mode, feed       string
		participants, capacity any
		official               bool
		batch, term            string
		allowed                bool
	}{
		{"zero sentinel overrides material", "04", "volunteered", 0, 0, true, "old", "old", false},
		{"wrong feed overrides material", "02", "volunteered", 2, 10, true, "round", "term", false},
		{"material overrides old metadata", "04", "volunteered", 2, 10, true, "old", "old", true},
		{"old batch", "02", "selected", 2, 10, false, "old", "term", false},
		{"old term", "02", "selected", 2, 10, false, "round", "old", false},
		{"missing counts not zero sentinel", "04", "volunteered", nil, nil, false, "", "", true},
	} {
		t.Run(test.name, func(t *testing.T) {
			row := map[string]any{"selected_count": test.participants, "weight_participant_count": test.participants, "capacity": test.capacity, "official_operation_available": test.official, "record_batch_code": test.batch, "record_term_code": test.term}
			ClassifyJWXKRecord(row, test.mode, "round", "term", test.feed)
			if row["operation_allowed"] != test.allowed || row["current_batch_record"] != test.allowed {
				t.Fatal(row)
			}
		})
	}
}

func TestJWXKSelectedAllFailuresDoNotBecomeEmptyResults(t *testing.T) {
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		if input.Path == "/xsxk/elective/user" {
			return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round"}]}}}`), nil
		}
		return []byte(`{"code":401,"msg":"不适用"}`), nil
	})
	if result, err := FetchJWXKSelected(context.Background(), requester, "direct", "round"); err == nil || result != nil {
		t.Fatal(result, err)
	}
}
