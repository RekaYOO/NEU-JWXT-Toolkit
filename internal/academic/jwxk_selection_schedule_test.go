package academic

import "testing"

func TestJWXKSelectionScheduleUsesCurrentModeAndPreservesUnknownTime(t *testing.T) {
	meeting := map[string]any{"meeting_id": "meeting", "weeks": []int{}, "recurrence_unknown": true, "teacher": ""}
	course := map[string]any{"class_id": "current", "course_code": "C1", "course_name": "课程", "teacher": "教师", "location": "教室", "campus": "01", "current_batch_record": true, "schedules": []map[string]any{meeting}, "experiment_schedules": []map[string]any{{"schedule_id": "optional-experiment"}}}
	selected := map[string]any{"selection_type_code": "04", "selected": []map[string]any{{"class_id": "wrong-mode", "current_batch_record": true}}, "volunteered": []map[string]any{course, course, {"class_id": "old", "current_batch_record": false}}, "complete": false, "unavailable_feeds": []string{"selected"}}
	result := JWXKSelectionSchedule(selected)
	courses := result["courses"].([]map[string]any)
	meetings := result["meetings"].([]map[string]any)
	if len(courses) != 1 || courses[0]["class_id"] != "current" || len(meetings) != 1 || result["complete"] != false {
		t.Fatal(result)
	}
	if meetings[0]["teacher"] != "教师" || meetings[0]["candidate_id"] != "current:0" || meetings[0]["recurrence_unknown"] != true {
		t.Fatal(meetings)
	}
	if meeting["teacher"] != "" || meeting["candidate_id"] != nil {
		t.Fatal("source snapshot mutated", meeting)
	}
	selected["selection_type_code"] = "02"
	result = JWXKSelectionSchedule(selected)
	if result["courses"].([]map[string]any)[0]["class_id"] != "wrong-mode" {
		t.Fatal(result)
	}
}
