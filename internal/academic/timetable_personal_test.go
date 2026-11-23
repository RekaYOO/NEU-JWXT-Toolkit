package academic

import "testing"

func TestTimetableSemanticIgnoresOrderingAndCurrentWeek(t *testing.T) {
	first := map[string]any{"weeks": []map[string]any{{"number": 1, "current": true}, {"number": 2, "current": false}}, "courses": []map[string]any{{"course_code": "B"}, {"course_code": "A"}}}
	second := map[string]any{"weeks": []map[string]any{{"number": 2, "current": true}, {"number": 1, "current": false}}, "courses": []map[string]any{{"course_code": "A"}, {"course_code": "B"}}}
	before, err := TimetableSemantic(first)
	if err != nil {
		t.Fatal(err)
	}
	after, err := TimetableSemantic(second)
	if err != nil || string(before) != string(after) {
		t.Fatalf("unstable revision: %s %s %v", before, after, err)
	}
}
