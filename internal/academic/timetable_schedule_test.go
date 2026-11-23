package academic

import (
	"context"
	"net/url"
	"reflect"
	"strings"
	"testing"
)

func TestWeeksPreserveParityExclusionsAndIgnoreMixedNumbers(t *testing.T) {
	for _, fixture := range []struct {
		value  string
		strict bool
		want   []int
	}{{"1-8周(单)，除第3周", false, []int{1, 5, 7}}, {"1-4周(单) 6-10周(双) / 教师2 / A301", true, []int{1, 3, 6, 8, 10}}, {"教室101 教师2", true, []int{}}, {"2,4,6", false, []int{2, 4, 6}}, {"32-35", false, []int{}}} {
		if got := ParseWeeks(fixture.value, fixture.strict); !reflect.DeepEqual(got, fixture.want) {
			t.Fatalf("%s: got %v want %v", fixture.value, got, fixture.want)
		}
	}
}

func TestTimetableScheduleAllModesAndAdditionalCourses(t *testing.T) {
	for _, mode := range []string{"personal", "class", "teacher", "room"} {
		t.Run(mode, func(t *testing.T) {
			requester := requestFunc(func(_ context.Context, _, target string, form url.Values) ([]byte, error) {
				if form.Get("XQDM") != "" || form.Has("ZC") {
					t.Fatal(form)
				}
				if mode != "personal" && (form.Get("CODE") != "target" || form.Get("KBLX") != timetableModes[mode]) {
					t.Fatal(form)
				}
				if mode == "personal" && !strings.Contains(target, "wdkbcx") {
					t.Fatal(target)
				}
				return []byte(`{"code":"0","datas":{"arrangedList":[{"courseName":"测试课程","courseCode":"TEST","teachClassId":"class","dayOfWeek":2,"beginSection":3,"endSection":4,"weeksAndTeachers":"1-8周(单)/教师甲","titleDetail":[{"text":"浑南校区 信息楼301"},{"text":"必修 考试/百分制"}],"tags":[{"text":"理论 预选"}],"color":"url(unsafe)"}],"notArrangeList":[{"courseName":"待排课程"}],"practiceList":[{"courseName":"集中实践"}]}}`), nil
			})
			query := TimetableSchedule{TimetableContext: TimetableContext{Mode: mode, Term: "2026-1", Target: "target", Campus: "all"}}
			result, err := FetchTimetableSchedule(context.Background(), requester, query)
			if err != nil {
				t.Fatal(err)
			}
			course := result["courses"].([]map[string]any)[0]
			if !reflect.DeepEqual(course["weeks"], []int{1, 3, 5, 7}) || course["preselected"] != true || course["color"] != "#2563eb" || course["location"] != "浑南校区 信息楼301" || course["grading_scheme"] != "百分制" || course["activity_type"] != "lecture" {
				t.Fatalf("course: %+v", course)
			}
			if !reflect.DeepEqual(course["teachers"], []string{"教师甲"}) || len(result["unscheduled"].([]map[string]any)) != 1 || len(result["practices"].([]map[string]any)) != 1 {
				t.Fatal(result)
			}
			repeated, _ := FetchTimetableSchedule(context.Background(), requester, query)
			if repeated["courses"].([]map[string]any)[0]["meeting_id"] != course["meeting_id"] {
				t.Fatal("unstable meeting identity")
			}
		})
	}
}

func TestTimetableUnknownRecurrenceAndMalformedPayload(t *testing.T) {
	query := TimetableSchedule{TimetableContext: TimetableContext{Mode: "personal", Term: "2026-1", Campus: "all"}}
	course := timetableCourse(map[string]any{"courseName": "未知周次"}, query)
	if course["recurrence_unknown"] != true {
		t.Fatal("unknown weeks treated as free")
	}
	week := 4
	query.Week = &week
	course = timetableCourse(map[string]any{"courseName": "当前周"}, query)
	if !reflect.DeepEqual(course["weeks"], []int{4}) || course["recurrence_unknown"] != false {
		t.Fatal(course)
	}
	for _, body := range []string{`{"code":"0","datas":{}}`, `{"datas":{"arrangedList":null}}`, `{"datas":{"arrangedList":[false]}}`} {
		requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) { return []byte(body), nil })
		if _, err := FetchTimetableSchedule(context.Background(), requester, query); err == nil {
			t.Fatalf("bad response accepted: %s", body)
		}
	}
}
