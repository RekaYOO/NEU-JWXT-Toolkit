package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestExperimentCoursesUseSeparateOfficialResultFeed(t *testing.T) {
	calls := []string{}
	requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		if method != "POST" {
			t.Fatal("unexpected request method")
		}
		calls = append(calls, target)
		if strings.HasSuffix(target, "queryAcademicYearSemester.do") {
			return []byte(`{"code":"0","datas":{"queryAcademicYearSemester":"2026-2027-1"}}`), nil
		}
		if form.Get("XNXQDM") != "2026-2027-1" {
			t.Fatal("wrong term")
		}
		if strings.HasSuffix(target, "queryCanSelectedCourses.do") {
			return []byte(`{"code":0,"datas":{"queryCanSelectedCourses":[{"taskId":"task","courseName":"可选课程","courseNo":"C1","mustDoProjectNums":1,"allExperimentProjectList":[{"projectCode":"project","selectedRoundId":"round","mustDo":true}]}]}}`), nil
		}
		if strings.HasSuffix(target, "queryStudentAllSelectCourseResult.do") {
			return []byte(`{"code":0,"datas":{"queryStudentAllSelectCourseResult":[{"taskId":"official","courseName":"官方结果课程","courseNo":"C2","projectResultList":[{"projectName":"结果项目","roundId":"official-round","isMustDo":"1","classWeeks":"1-8周","classDays":"星期一","classSessions":"1-2节","classrooms":"实验楼"}]}]}}`), nil
		}
		t.Fatal("unregistered endpoint")
		return nil, nil
	})
	payload, err := FetchExperimentCourses(context.Background(), requester, "")
	if err != nil {
		t.Fatal(err)
	}
	course := payload["courses"].([]map[string]any)[0]
	if course["selected_count"] != 1 || course["is_complete"] != true {
		t.Fatal(course)
	}
	result := payload["selected_results"].([]map[string]any)[0]
	if len(calls) != 3 || result["course_name"] != "官方结果课程" || result["course_no"] != "C2" || result["location"] != "实验楼" {
		t.Fatal("result reconstructed from picker or schedule missing", result)
	}
}

func TestExperimentRejectsFailedOrMalformedLists(t *testing.T) {
	for _, body := range []string{`{"code":1,"datas":{"queryCanSelectedCourses":[]}}`, `{"code":0,"datas":{}}`, `{"code":0,"datas":{"queryCanSelectedCourses":{}}}`, `{"code":0,"datas":{"queryCanSelectedCourses":[null]}}`, `<html>login</html>`} {
		requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) { return []byte(body), nil })
		if _, err := FetchExperimentCourses(context.Background(), requester, "2026-2027-1"); err == nil {
			t.Fatal("failed read masqueraded as empty courses")
		}
	}
}

func TestExperimentRoundsConflictFreshnessAndSelectionWindow(t *testing.T) {
	query := ExperimentQuery{Term: "2026-2027-1", Task: "task", Course: "course", Project: "project"}
	baseline := []SelectionMeeting{{ID: "meeting", Code: "course", Name: "理论课", Term: query.Term, Weeks: []int{1, 2, 3}, Day: 1, Start: 2, End: 3}}
	now := time.Date(2026, 8, 10, 4, 0, 0, 0, time.UTC)
	for _, scenario := range []struct {
		name                       string
		stale, available, official bool
		start, end, expected       string
		canSelect                  bool
	}{
		{"fresh", false, true, false, "", "", "conflict", false},
		{"stale", true, true, false, "", "", "unknown", true},
		{"missing", false, false, false, "", "", "unknown", true},
		{"official", true, false, true, "", "", "conflict", false},
		{"future", false, false, false, "2026/08/11", "2026/08/20", "unknown", false},
		{"ended", false, false, false, "2026-08-01 08:00", "2026-08-09 18:00", "unknown", false},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			round := map[string]any{"wid": "round", "round_name": "实验班", "week": "1-8周", "day": "星期一", "time": "1-2节", "capacity": 20, "selected_count": 1, "conflict": scenario.official, "select_start": scenario.start, "select_end": scenario.end}
			result := ExperimentRoundAvailability(round, query, baseline, scenario.available, scenario.stale, now)
			if result["conflict_status"] != scenario.expected || result["can_select"] != scenario.canSelect {
				t.Fatal(result)
			}
			if scenario.available {
				matches := result["conflicts"].([]map[string]any)
				if len(matches) != 1 || matches[0]["start_section"] != 2 || matches[0]["end_section"] != 2 {
					t.Fatal("same-course experiment clash lost or intersection incorrect", matches)
				}
			}
		})
	}
}

func TestExperimentEmptySemesterAndMalformedRoundQuery(t *testing.T) {
	calls := 0
	requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
		calls++
		return []byte(`{"code":0,"datas":{"queryAcademicYearSemester":""}}`), nil
	})
	payload, err := FetchExperimentCourses(context.Background(), requester, "")
	if err != nil || calls != 1 || payload["total"] != 0 {
		t.Fatal(payload, err)
	}
	if _, err := FetchExperimentRounds(context.Background(), requester, ExperimentQuery{}); err == nil || calls != 1 {
		t.Fatal("invalid query reached school")
	}
}

func TestExperimentRoundsSendCompleteOfficialIdentifiers(t *testing.T) {
	query := ExperimentQuery{Term: "2026-2027-1", Task: "task", Course: "course", Project: "project"}
	requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		if method != "POST" || !strings.HasSuffix(target, "/xsxk/queryTaskProjectRounds.do") || form.Get("SYRW_WID") != query.Task || form.Get("RWID") != query.Task || form.Get("SYXMDM") != query.Project || form.Get("KCH") != query.Course || form.Get("XNXQDM") != query.Term {
			t.Fatal("incomplete experiment lookup")
		}
		return []byte(`{"code":0,"datas":{"queryTaskProjectRounds":[{"wid":"round","courseCapacity":2,"selectedNums":2,"conflict":"1","selected":true}]}}`), nil
	})
	rounds, err := FetchExperimentRounds(context.Background(), requester, query)
	if err != nil || len(rounds) != 1 {
		t.Fatal(err)
	}
	result := ExperimentRoundAvailability(rounds[0], query, nil, false, true, time.Now())
	if result["is_full"] != true || result["can_select"] != false || result["selected"] != true || result["conflict_source"] != "official" {
		t.Fatal(result)
	}
}

func TestExperimentRevisionAndDiffIgnoreTopLevelOrder(t *testing.T) {
	previous := json.RawMessage(`{"term":"2026-2027-1","courses":[{"task_id":"b","course_name":"B"},{"task_id":"a","course_name":"A"}],"selected_results":[{"task_id":"b","project_code":"p"},{"task_id":"a","project_code":"p"}]}`)
	current := json.RawMessage(`{"term":"2026-2027-1","courses":[{"task_id":"a","course_name":"A"},{"task_id":"b","course_name":"B"}],"selected_results":[{"task_id":"a","project_code":"p"},{"task_id":"b","project_code":"p"}]}`)
	diff := ExperimentDiff(previous, current)
	if diff["selected_results_changed"] != false || diff["term_changed"] != false || len(diff["changed_course_ids"].([]string)) != 0 {
		t.Fatal(diff)
	}
	current = json.RawMessage(`{"term":"2026-2027-1","courses":[{"task_id":"a","course_name":"changed"},{"task_id":"c"}],"selected_results":[]}`)
	diff = ExperimentDiff(previous, current)
	if diff["selected_results_changed"] != true || len(diff["changed_course_ids"].([]string)) != 1 || len(diff["added_course_ids"].([]string)) != 1 || len(diff["removed_course_ids"].([]string)) != 1 {
		t.Fatal(diff)
	}
}
