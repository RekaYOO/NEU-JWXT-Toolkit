package academic

import (
	"encoding/json"
	"testing"
)

func TestJWXKArchiveUnionFiltersAndJSONMeetings(t *testing.T) {
	previous := []map[string]any{{"class_id": "one", "course_code": "A", "course_name": "计算机", "source_scopes": []string{"FANKC"}, "teaching_class_type": "FANKC", "teacher": "老师", "schedules": []map[string]any{{"weekday": 2, "start_section": 3, "end_section": 4, "campus": "1"}}}}
	incoming := []map[string]any{{"class_id": "one", "course_code": "A", "source_scopes": []string{"ALLKC"}, "teaching_class_type": "ALLKC", "selected_count": 5}, {"class_id": "two", "course_code": "B", "source_scopes": []string{"ALLKC"}}}
	courses := MergeJWXKArchive(previous, incoming)
	encoded, _ := json.Marshal(courses)
	json.Unmarshal(encoded, &courses)
	input := JWXKCatalogSearch{Scope: "ROUND", Page: 1, Size: 20, Keyword: "老师", OnlineMode: "all", TimeSlot: &JWXKTimeSlot{Weekday: 2, Section: 4}}
	result := QueryJWXKArchive(courses, input)
	if result["total"] != 1 || courses[0]["teaching_class_type"] != "FANKC" || result["catalog_complete"] != false {
		t.Fatal(result, courses)
	}
	input.TimeSlot.Section = 5
	if result := QueryJWXKArchive(courses, input); result["total"] != 0 {
		t.Fatal(result)
	}
	input.TimeSlot, input.Keyword, input.Filters = nil, "", map[string]string{"SFYX": "1"}
	if result := QueryJWXKArchive(courses, input); result["total"] != 0 {
		t.Fatal(result)
	}
	if _, exists := previous[0]["selected_count"]; exists {
		t.Fatal("merge changed source")
	}
}
