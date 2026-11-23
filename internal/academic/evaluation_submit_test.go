package academic

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

func evaluationSubmitFixture() (map[string]any, map[string]any, EvaluationInput) {
	options := []map[string]any{{"value": 6, "df": 100}, {"value": 5, "df": 90}, {"value": 2, "df": 60}, {"value": 1, "df": 50}}
	return map[string]any{"kkdwid": "dept", "pjsjid": "teacher", "pjsc": 8}, map[string]any{"libid": "library", "indicators": []map[string]any{
		{"zbid": "first", "evaltype": 1, "sfbt": 1, "level_json": options},
		{"zbid": "second", "evaltype": 1, "sfbt": 1, "sfdx": 1, "level_json": options},
		{"zbid": "text", "evaltype": 2, "sfbt": 1},
	}}, EvaluationInput{Task: "task", Record: "record", Strategy: "highest", Texts: map[string]string{"text": "private feedback"}, Reason: "private reason"}
}

func TestEvaluationStrategyPreservesTextAndOfficialPayload(t *testing.T) {
	course, target, input := evaluationSubmitFixture()
	payload, summary, err := BuildEvaluationSubmission(course, target, "term", input)
	if err != nil {
		t.Fatal(err)
	}
	var wire map[string]any
	json.Unmarshal(payload, &wire)
	rows := wire["resultList"].([]any)
	if Object(rows[0])["dfdj"] != "5" || Object(rows[1])["dfdj"] != "6" || Object(rows[2])["subjectContent"] != "private feedback" || Object(rows[0])["taskid"] != "record" || Object(rows[0])["rwid"] != "task" || Object(wire["task"])["dfyy"] != "private reason" {
		t.Fatal(wire)
	}
	if summary["average_score"] != float64(95) || summary["text_count"] != 1 {
		t.Fatal(summary)
	}
	encoded, _ := json.Marshal(summary)
	if strings.Contains(string(encoded), "private") {
		t.Fatal("summary leaked text")
	}
}

func TestEvaluationCustomValidationUsesActualWireScores(t *testing.T) {
	for _, scores := range []map[string]json.RawMessage{
		{"first": json.RawMessage(`6`), "second": json.RawMessage(`[6,5]`)},
		{"first": json.RawMessage(`4`), "second": json.RawMessage(`6`)},
		{"first": json.RawMessage(`[5,6]`), "second": json.RawMessage(`6`)},
		{"wrong": json.RawMessage(`5`), "second": json.RawMessage(`6`)},
		{"first": json.RawMessage(`5`)},
	} {
		course, target, input := evaluationSubmitFixture()
		input.Strategy = "custom"
		input.Scores = scores
		if _, _, err := BuildEvaluationSubmission(course, target, "term", input); err == nil {
			t.Fatal(scores)
		}
	}
	course, target, input := evaluationSubmitFixture()
	input.Texts = nil
	if _, _, err := BuildEvaluationSubmission(course, target, "term", input); err == nil {
		t.Fatal("required text omitted")
	}
	for _, raw := range []string{`null`, `"5"`, `5.1`, `[]`, `[5,5]`, `7`, `[5,"6"]`} {
		if _, err := evaluationSelection(json.RawMessage(raw)); err == nil {
			t.Fatal(raw)
		}
	}
}

func TestEvaluationOfficialOptionSubsetAndWeights(t *testing.T) {
	course, target, input := evaluationSubmitFixture()
	options := []map[string]any{{"value": 2, "df": 32}, {"value": 5, "df": 85}}
	for _, indicator := range target["indicators"].([]map[string]any) {
		if indicator["evaltype"] == 1 {
			indicator["level_json"] = options
		}
	}
	payload, summary, err := BuildEvaluationSubmission(course, target, "term", input)
	if err != nil || summary["average_score"] != 58.5 {
		t.Fatalf("%s %v %v", payload, summary, err)
	}
}

func TestEvaluationUnknownResponseNeverBecomesSuccess(t *testing.T) {
	for _, body := range []string{`{}`, `{"message":"private upstream secret"}`, `{"message":"重复提交"}`, `<html>login</html>`, `"success" trailing`} {
		requester := evaluationRequestFunc(func(context.Context, string, json.RawMessage) ([]byte, error) { return []byte(body), nil })
		if state := SendEvaluation(context.Background(), requester, json.RawMessage(`{}`)); state != "unknown" {
			t.Fatal(state)
		}
	}
	calls := 0
	requester := evaluationRequestFunc(func(context.Context, string, json.RawMessage) ([]byte, error) {
		calls++
		return nil, errors.New("connection lost")
	})
	if state := SendEvaluation(context.Background(), requester, json.RawMessage(`{}`)); state != "unknown" || calls != 1 {
		t.Fatal("uncertain submit repeated")
	}
}

func TestEvaluationPreflightRejectsIdentityEligibilityAndTime(t *testing.T) {
	for _, change := range []map[string]any{{"pjrid": "other"}, {"iskpj": "0"}, {"begindate": time.Now().Add(time.Hour).UnixMilli()}, {"enddate": time.Now().Add(-time.Hour).UnixMilli()}} {
		row := map[string]any{"taskid": "task", "xspjid": "record", "issubmit": "1", "iskpj": "1", "pjrid": "student-a"}
		for key, value := range change {
			row[key] = value
		}
		calls := 0
		requester := evaluationRequestFunc(func(context.Context, string, json.RawMessage) ([]byte, error) {
			calls++
			if calls > 1 {
				t.Fatal("invalid course reached indicators or submission")
			}
			return json.Marshal(map[string]any{"rowCount": 1, "items": []any{row}})
		})
		if _, err := PrepareEvaluation(context.Background(), requester, "student-a", EvaluationInput{Task: "task", Record: "record", Cycle: "term"}); err == nil {
			t.Fatal(change)
		}
	}
}
