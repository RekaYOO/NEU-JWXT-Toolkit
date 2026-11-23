package academic

import "testing"

func TestSelectionConflictPrecedenceAndIdentity(t *testing.T) {
	baseline := NormalizeSelectionMeeting(map[string]any{"course_code": "A", "course_name": "同名", "weeks": []int{1, 3}, "weekday": 1, "start_section": 1, "end_section": 2}, "term", "personal_timetable")
	for _, test := range []struct {
		name      string
		row       map[string]any
		available bool
		status    string
	}{
		{"different code same name conflicts", map[string]any{"course_code": "B", "course_name": "同名", "weeks": []int{3}, "weekday": 1, "start_section": 2, "end_section": 3}, true, "conflict"},
		{"same code ignored", map[string]any{"course_code": "A", "course_name": "别名", "weeks": []int{3}, "weekday": 1, "start_section": 2, "end_section": 3}, true, "clear"},
		{"disjoint weeks", map[string]any{"course_code": "B", "weeks": []int{2}, "weekday": 1, "start_section": 2, "end_section": 3}, true, "clear"},
		{"unknown weeks", map[string]any{"course_code": "B", "weekday": 1, "start_section": 2, "end_section": 3}, true, "unknown"},
		{"missing baseline", map[string]any{"course_code": "B", "weeks": []int{2}, "weekday": 1, "start_section": 2, "end_section": 3}, false, "unknown"},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := NormalizeSelectionMeeting(test.row, "term", "jwxk_plan")
			result := PreviewSelectionConflicts("term", []SelectionMeeting{baseline}, []SelectionMeeting{candidate}, test.available, false)
			if result["results"].([]map[string]any)[0]["status"] != test.status {
				t.Fatal(result)
			}
		})
	}
}

func TestSelectionClockBoundaryAndInternalConflicts(t *testing.T) {
	first := NormalizeSelectionMeeting(map[string]any{"course_code": "A", "weekText": "1-4周", "dayOfWeek": "周一", "time": "08:00-09:00"}, "term", "jwxk_plan")
	second := NormalizeSelectionMeeting(map[string]any{"course_code": "B", "weeks": []int{2}, "weekday": 1, "start_time": "09:00", "end_time": "10:00"}, "term", "jwxk_plan")
	if match := compareSelectionMeetings(first, second, "selection_plan"); match != nil {
		t.Fatal(match)
	}
	second.StartTime = "08:30"
	result := PreviewSelectionConflicts("term", nil, []SelectionMeeting{first, second}, false, true)
	for _, row := range result["results"].([]map[string]any) {
		if row["status"] != "conflict" {
			t.Fatal(row)
		}
	}
	second.StartTime = "bad"
	if match := compareSelectionMeetings(first, second, "selection_plan"); match["status"] != "unknown" {
		t.Fatal(match)
	}
}
