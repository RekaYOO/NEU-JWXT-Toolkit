package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"
	"testing"
)

type evaluationRequestFunc func(context.Context, string, json.RawMessage) ([]byte, error)

func (request evaluationRequestFunc) EvaluationRequest(ctx context.Context, path string, body json.RawMessage) ([]byte, error) {
	return request(ctx, path, body)
}

func TestEvaluationCoursesPaginationAndOfficialSubmitMeaning(t *testing.T) {
	pages := 0
	requester := evaluationRequestFunc(func(_ context.Context, path string, body json.RawMessage) ([]byte, error) {
		if strings.HasSuffix(path, "findCycle") {
			return []byte(`[{"value":"2025-2026-2","isdefault":"1"}]`), nil
		}
		pages++
		var query map[string]any
		json.Unmarshal(body, &query)
		if query["xnxq"] != "2025-2026-2" || query["taskid"] != "task" || Number(Object(query["page"])["pageIndex"]) != float64(pages) {
			t.Fatal(query)
		}
		if pages == 1 {
			return []byte(`{"rowCount":2,"items":[{"taskid":"task","xspjid":"completed","issubmit":"0","kcmc":"已评课程"}]}`), nil
		}
		return []byte(`{"rowCount":2,"items":[{"taskid":"task","xspjid":"pending","issubmit":"1","kcmc":"待评课程"}]}`), nil
	})
	result, err := FetchEvaluationCourses(context.Background(), requester, "task", "")
	if err != nil || result["completed"] != 1 || result["pending"] != 1 || pages != 2 {
		t.Fatalf("%v %v", result, err)
	}
	courses := result["courses"].([]map[string]any)
	if courses[0]["is_evaluated"] != true || courses[1]["is_evaluated"] != false {
		t.Fatal(courses)
	}
}

func TestEvaluationRejectsMalformedAndMismatchedCourses(t *testing.T) {
	for _, body := range []string{`{}`, `{"rowCount":1,"items":[]}`, `{"rowCount":1,"items":[{"taskid":"other","xspjid":"record","issubmit":"1"}]}`, `{"rowCount":1,"items":[{"taskid":"task","xspjid":"record","issubmit":"unknown"}]}`, `{"rowCount":2,"items":[{"taskid":"task","xspjid":"record","issubmit":"1"}]}`, `{"rowCount":0,"items":[]} trailing`} {
		requester := evaluationRequestFunc(func(context.Context, string, json.RawMessage) ([]byte, error) { return []byte(body), nil })
		if _, err := FetchEvaluationCourses(context.Background(), requester, "task", "2025-2026-2"); err == nil {
			t.Fatal(body)
		}
	}
}

func TestEvaluationIndicatorsUseCourseLibraryAndRecord(t *testing.T) {
	requester := evaluationRequestFunc(func(_ context.Context, path string, _ json.RawMessage) ([]byte, error) {
		switch {
		case strings.HasSuffix(path, "findJdpXqByTaskid"):
			return []byte(`{"rowCount":1,"items":[{"taskid":"task","xspjid":"record","issubmit":"0","libid":"correct"}]}`), nil
		case strings.Contains(path, "getTasklib/"):
			return []byte(`[{"id":"wrong","libname":"其他"},{"id":"correct","libname":"本课指标"}]`), nil
		default:
			parsed, _ := url.Parse(path)
			if parsed.Query().Get("taskid") != "record" || parsed.Query().Get("libtaskid") != "task" || parsed.Query().Get("libid") != "correct" {
				t.Fatal(path)
			}
			return []byte(`{"zbkzf":100,"list":[{"zbid":"choice","evaltype":1,"dfdj":"6,5","leveljson":"[{\"value\":6,\"df\":85},{\"value\":5,\"df\":75}]"},{"zbid":"text","evaltype":2,"subjectContent":"建议"}]}`), nil
		}
	})
	result, err := FetchEvaluationIndicators(context.Background(), requester, "task", "record", "2025-2026-2")
	if err != nil || result["libid"] != "correct" || result["avg_score"] != float64(85) || result["text_count"] != 1 {
		t.Fatalf("%v %v", result, err)
	}
	indicators := result["indicators"].([]map[string]any)
	if indicators[1]["result"] != "建议" {
		t.Fatal(indicators)
	}
	if _, err := FetchEvaluationIndicators(context.Background(), requester, "task", "unknown", "2025-2026-2"); err == nil {
		t.Fatal("unknown course accepted")
	}
}
