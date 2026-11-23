package academic

import (
	"encoding/json"
	"testing"
)

func TestScoresDiffIgnoresReferenceAndRetainsCourseChanges(t *testing.T) {
	before := json.RawMessage(`{"scores":[{"code":"A","term":"T","score":"70","detail_ref":"old"},{"code":"B","term":"T","score":"80"}],"overall_gpa":3}`)
	after := json.RawMessage(`{"scores":[{"code":"A","term":"T","score":"90","detail_ref":"new"},{"code":"C","term":"T","score":"60"}],"overall_gpa":3.5}`)
	diff := ScoresDiff(before, after)
	counts := diff["counts"].(map[string]int)
	if counts["added"] != 1 || counts["removed"] != 1 || counts["changed"] != 1 || diff["overall_gpa_changed"] != true {
		t.Fatalf("diff: %+v", diff)
	}
	same := ScoresDiff(before, json.RawMessage(`{"scores":[{"code":"B","term":"T","score":"80"},{"code":"A","term":"T","score":"70","detail_ref":"rotated"}],"overall_gpa":3}`))
	if same["counts"].(map[string]int)["changed"] != 0 || same["overall_gpa_changed"] != false {
		t.Fatalf("reference triggered change: %+v", same)
	}
}

func TestReportDiffIgnoresOrderAndTimestamp(t *testing.T) {
	before := json.RawMessage(`{"categories":[{"name":"A","courses":[{"course_code":"1"},{"course_code":"2"}]}],"calculated_time":"before","credit_summary":{"total_required":8}}`)
	after := json.RawMessage(`{"categories":[{"name":"A","courses":[{"course_code":"2"},{"course_code":"1"}]}],"calculated_time":"after","credit_summary":{"total_required":8}}`)
	diff := ReportDiff(before, after)
	if diff["category_tree_changed"] != false || diff["credit_summary_changed"] != false {
		t.Fatalf("cosmetic change: %+v", diff)
	}
}
