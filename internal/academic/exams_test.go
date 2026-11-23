package academic

import (
	"context"
	"net/url"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

func TestExamsCurrentTermAndStatusCounts(t *testing.T) {
	calls := 0
	requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		calls++
		if strings.HasSuffix(target, "xnxq.do") {
			return []byte(`{"code":"0","datas":[{"itemCode":"2025-1","itemName":"旧学期"},{"itemCode":"2026-1","itemName":"当前学期","selected":true}]}`), nil
		}
		if method != "POST" || form.Get("termCode") != "2026-1" {
			t.Fatalf("request: %s %v", method, form)
		}
		return []byte(`{"code":"0","datas":[{"taskId":"A","examStatus":0,"examSeatNo":8},{"taskId":"B","examStatus":1},{"taskId":"C","examStatus":2}]}`), nil
	})
	result, err := FetchExams(context.Background(), requester, "")
	if err != nil || calls != 2 || result["term_name"] != "当前学期" || result["upcoming"] != 1 || result["ongoing"] != 1 || result["finished"] != 1 {
		t.Fatalf("result: %+v %v", result, err)
	}
	if result["exams"].([]map[string]any)[0]["exam_seat_no"] != "8" {
		t.Fatal("seat normalization failed")
	}
}

func TestExamDetailsDistinguishEmptyFromFailure(t *testing.T) {
	for _, sample := range []struct {
		name, payload string
		valid         bool
	}{
		{"empty", `{"code":"0","datas":[]}`, true},
		{"invalid shape", `{"code":"0","datas":{}}`, false},
		{"upstream failure", `{"code":"1","datas":[]}`, false},
	} {
		t.Run(sample.name, func(t *testing.T) {
			requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
				if strings.HasSuffix(target, "xnxq.do") {
					return []byte(`{"code":"0","datas":[{"itemCode":"2026-1","selected":true}]}`), nil
				}
				if method != "POST" || !strings.HasSuffix(target, "student/exams.do") || form.Get("termCode") != "2026-1" {
					t.Fatalf("unexpected exam request: %s %s %v", method, target, form)
				}
				return []byte(sample.payload), nil
			})
			result, err := FetchExams(context.Background(), requester, "2026-1")
			if !sample.valid {
				if err == nil || result != nil {
					t.Fatal("failed response returned an exam list", result, err)
				}
				return
			}
			if err != nil || result["total"] != 0 || len(result["exams"].([]map[string]any)) != 0 {
				t.Fatal(result, err)
			}
		})
	}
}

func TestExamCalendarUTCStableIdentityAndSafeText(t *testing.T) {
	exams := []map[string]any{{"task_id": "task", "course_name": strings.Repeat("中文课程", 20) + "\r\nBEGIN:VEVENT", "exam_type": "期末", "exam_place": "楼;室,座位", "exam_time_description": "2026年05月08日 10:10-12:10(星期五第1场)"}, {"task_id": "unknown", "exam_time_description": "待定"}}
	now := time.Date(2026, 5, 1, 12, 0, 0, 0, time.FixedZone("Asia/Shanghai", 8*3600))
	calendar, count := ExamsCalendar(exams, "anonymous", "2026-1", now)
	if count != 1 || !strings.Contains(calendar, "DTSTART:20260508T021000Z") || !strings.Contains(calendar, "DTSTAMP:20260501T040000Z") || !strings.Contains(calendar, "TRIGGER:-PT24H") || !strings.Contains(calendar, "TRIGGER:-PT2H") {
		t.Fatalf("calendar: %s", calendar)
	}
	if strings.Count(calendar, "\r\nBEGIN:VEVENT\r\n") != 1 {
		t.Fatal("text injected an event")
	}
	unfolded := strings.ReplaceAll(calendar, "\r\n ", "")
	if !strings.Contains(unfolded, "SUMMARY:【期末】"+strings.Repeat("中文课程", 20)+"\\nBEGIN:VEVENT\r\n") {
		t.Fatal("folded summary lost course text or newline")
	}
	for _, line := range strings.Split(calendar, "\r\n") {
		if len(line) > 75 || !utf8.ValidString(line) {
			t.Fatalf("invalid folded line: %q", line)
		}
	}
	other, _ := ExamsCalendar(exams, "anonymous", "2026-1", now.Add(time.Hour))
	uid := func(value string) string {
		for _, line := range strings.Split(value, "\r\n") {
			if strings.HasPrefix(line, "UID:") {
				return line
			}
		}
		return ""
	}
	if uid(calendar) == "" || uid(calendar) != uid(other) {
		t.Fatal("export changes event identity")
	}
}

func TestExamsRejectMalformedUpstreamAndInvalidDates(t *testing.T) {
	requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
		return []byte(`{"code":"0","datas":{}}`), nil
	})
	if _, err := FetchExamTerms(context.Background(), requester); err == nil {
		t.Fatal("bad response interpreted as no exams")
	}
	for _, value := range []string{"2026年02月30日 10:00-11:00", "2026年05月08日 12:00-10:00", "待定"} {
		if _, _, ok := examTimes(value); ok {
			t.Fatalf("invalid date accepted: %s", value)
		}
	}
}
