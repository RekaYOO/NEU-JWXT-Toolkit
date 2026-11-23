package academic

import (
	"encoding/json"
	"testing"
)

func TestScheduleChecksOnlyPersonalBaselineAndHonorsIdentity(t *testing.T) {
	baseline := SelectionMeeting{ID: "base", SourceID: "base", Term: "term", Code: "C1", Class: "class1", Name: "课程", Weeks: []int{1, 2}, Day: 1, Start: 1, End: 2}
	candidate := baseline
	candidate.ID = "candidate"
	for _, scenario := range []struct {
		available, stale, ignore bool
		expected                 string
	}{{true, false, false, "conflict"}, {true, false, true, "clear"}, {true, true, false, "unknown"}, {false, true, false, "unknown"}} {
		results := CheckScheduleConflicts([]SelectionMeeting{baseline}, []SelectionMeeting{candidate}, scenario.available, scenario.stale, scenario.ignore)
		if results[0]["status"] != scenario.expected {
			t.Fatal(scenario, results)
		}
	}
	other := candidate
	other.Code = "C2"
	other.ID = "other"
	results := CheckScheduleConflicts(nil, []SelectionMeeting{candidate, other}, true, false, false)
	if results[0]["status"] != "clear" || results[1]["status"] != "clear" {
		t.Fatal("candidate-only overlap treated as personal conflict", results)
	}
	results = CheckScheduleConflicts([]SelectionMeeting{baseline}, []SelectionMeeting{other}, true, false, false)
	match := results[0]["matches"].([]map[string]any)[0]
	if match["baseline_course_code"] != "C1" || match["baseline_teaching_class_id"] != "class1" || len(match["baseline_weeks"].([]int)) != 2 {
		t.Fatal(match)
	}
}

func TestScheduleCandidateValidationAndDefaultWeek(t *testing.T) {
	for _, raw := range []string{`{"course_name":"课","weekday":8}`, `{"course_name":"课","weeks":[31]}`, `{"course_name":"课","start_section":4,"end_section":2}`, `{"course_name":"课","start_time":"08:00"}`, `{"course_name":"课","extra":true}`, `{"course_name":"课","weekday":1.5}`} {
		var row map[string]any
		json.Unmarshal([]byte(raw), &row)
		if ValidScheduleCandidate(row) {
			t.Fatal(raw)
		}
	}
	row := map[string]any{"course_name": "课", "weekday": float64(1), "start_section": float64(1), "end_section": float64(2)}
	if !ValidScheduleCandidate(row) {
		t.Fatal("valid course rejected")
	}
	week := 3
	meeting := NormalizeScheduleMeeting(row, "term", "candidate", &week)
	if len(meeting.Weeks) != 1 || meeting.Weeks[0] != 3 {
		t.Fatal(meeting)
	}
	if _, changed := row["weeks"]; changed {
		t.Fatal("normalization mutated request")
	}
}
