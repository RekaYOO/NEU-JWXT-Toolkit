package academic

import (
	"context"
	"strconv"
)

func JWXKSelectionSchedule(selected map[string]any) map[string]any {
	feed := "selected"
	if Text(selected["selection_type_code"]) == "04" {
		feed = "volunteered"
	}
	courses, meetings := []map[string]any{}, []map[string]any{}
	seen := map[string]bool{}
	rows, _ := selected[feed].([]map[string]any)
	for _, course := range rows {
		if course["current_batch_record"] != true {
			continue
		}
		identity := firstText(course["class_id"], course["course_code"])
		if identity != "" && seen[identity] {
			continue
		}
		if identity != "" {
			seen[identity] = true
		}
		courses = append(courses, course)
		arrangements, _ := course["schedules"].([]map[string]any)
		for index, original := range arrangements {
			meeting := map[string]any{}
			for key, value := range original {
				meeting[key] = value
			}
			meeting["candidate_id"] = identity + ":" + strconv.Itoa(index)
			meeting["course_code"], meeting["course_name"], meeting["teaching_class_id"] = Text(course["course_code"]), Text(course["course_name"]), Text(course["class_id"])
			for _, field := range []string{"teacher", "location", "campus"} {
				meeting[field] = firstText(meeting[field], course[field])
			}
			meetings = append(meetings, meeting)
		}
	}
	return map[string]any{"source": "selected_records_fallback", "source_label": "根据官方已选记录生成", "courses": courses, "meetings": meetings, "complete": selected["complete"], "unavailable_feeds": selected["unavailable_feeds"]}
}

func FetchJWXKSelectionSchedule(ctx context.Context, requester ServiceRequester, mode, batch string) (map[string]any, error) {
	selected, err := FetchJWXKSelected(ctx, requester, mode, batch)
	if err != nil {
		return nil, err
	}
	return JWXKSelectionSchedule(selected), nil
}
