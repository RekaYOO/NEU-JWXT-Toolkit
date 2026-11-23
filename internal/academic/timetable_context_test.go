package academic

import (
	"context"
	"net/url"
	"strings"
	"testing"
)

func TestTimetableModesCampusAndWeekSemantics(t *testing.T) {
	for _, mode := range []string{"personal", "class", "teacher", "room"} {
		t.Run(mode, func(t *testing.T) {
			sections := 0
			requester := requestFunc(func(_ context.Context, _, target string, form url.Values) ([]byte, error) {
				if form.Get("XNXQDM") != "2026-1" {
					t.Fatal(form)
				}
				if strings.HasSuffix(target, "getMyScheduledCampus.do") {
					if mode != "personal" && (form.Get("CODE") != "target" || form.Get("KBLX") != timetableModes[mode]) {
						t.Fatalf("mode parameters: %v", form)
					}
					return []byte(`{"code":"0","datas":[]}`), nil
				}
				if strings.HasSuffix(target, "getTermWeeks.do") {
					return []byte(`{"datas":[{"serialNumber":1,"startTime":"2026-03-02 00:00:00","endTime":"2026-03-08 00:00:00","curWeek":true}]}`), nil
				}
				sections++
				if mode != "personal" {
					t.Fatal("empty target campus fetched sections")
				}
				if form.Get("XQDM") != "" {
					t.Fatal("all-campus not normalized")
				}
				return []byte(`[{"sectionNumber":1,"startTime":"08:00","endTime":"08:45"}]`), nil
			})
			result, err := FetchTimetableContext(context.Background(), requester, TimetableContext{Mode: mode, Term: "2026-1", Target: "target"})
			if err != nil {
				t.Fatal(err)
			}
			campuses := result["campuses"].([]map[string]any)
			if mode == "personal" {
				if len(campuses) != 1 || campuses[0]["code"] != "all" || sections != 1 {
					t.Fatal(result)
				}
			} else if len(campuses) != 0 || sections != 0 {
				t.Fatal(result)
			}
			week := result["weeks"].([]map[string]any)[0]
			if week["start_date"] != "2026-03-01" || week["end_date"] != "2026-03-07" || week["current"] != true {
				t.Fatalf("week normalization: %+v", week)
			}
		})
	}
}

func TestTimetableCurrentTermResponseVariants(t *testing.T) {
	for _, fixture := range []string{`{"datas":{"cxmrxnxq":"2026-1"}}`, `{"datas":{"cxmrxnxq":{"rows":[{"CSZA":"2026-1"}]}}}`, `{"datas":{"DQXNXQDM":"2026-1"}}`} {
		requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
			if strings.HasSuffix(target, "cxmrxnxq.do") {
				return []byte(fixture), nil
			}
			return []byte(`{"datas":{"xnxqcx":{"rows":[{"DM":"2026-1","MC":"当前学期"}]}}}`), nil
		})
		result, err := FetchTimetableTerms(context.Background(), requester)
		if err != nil || result["current"] != "2026-1" || result["terms"].([]map[string]any)[0]["current"] != true {
			t.Fatalf("terms: %+v %v", result, err)
		}
	}
}

func TestTimetableContextRejectsMissingTargetAndMalformedCampus(t *testing.T) {
	if (TimetableContext{Mode: "room", Term: "2026-1"}).Valid() {
		t.Fatal("missing target accepted")
	}
	requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
		return []byte(`{"code":"0","datas":{"unexpected":true}}`), nil
	})
	if _, err := FetchTimetableContext(context.Background(), requester, TimetableContext{Mode: "room", Term: "2026-1", Target: "target"}); err == nil {
		t.Fatal("malformed campus interpreted as no courses")
	}
}
