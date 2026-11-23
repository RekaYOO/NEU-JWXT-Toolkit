package academic

import (
	"math"
	"regexp"
	"unicode/utf8"
)

var scheduleSource = regexp.MustCompile(`^[A-Za-z0-9_-]*$`)

func ValidScheduleCandidate(row map[string]any) bool {
	if row == nil || Text(row["course_name"]) == "" {
		return false
	}
	limits := map[string]int{"id": 128, "meeting_id": 128, "candidate_id": 128, "source": 32, "source_id": 128, "course_name": 200, "course_code": 128, "teaching_class_id": 128, "activity_type": 32, "course_type": 100, "week_text": 200, "week": 200, "day": 50, "start_time": 8, "end_time": 8, "time": 100, "location": 300, "campus": 100}
	for key, value := range row {
		if limit, ok := limits[key]; ok {
			text, ok := value.(string)
			if !ok || utf8.RuneCountInString(text) > limit {
				return false
			}
			continue
		}
		switch key {
		case "weekday", "start_section", "end_section":
			number, ok := value.(float64)
			limit := 30.0
			if key == "weekday" {
				limit = 7
			}
			if !ok || number < 0 || number > limit || number != math.Trunc(number) {
				return false
			}
		case "weeks":
			values, ok := value.([]any)
			if !ok || len(values) > 30 {
				return false
			}
			for _, value := range values {
				number, ok := value.(float64)
				if !ok || number < 1 || number > 30 || number != math.Trunc(number) {
					return false
				}
			}
		case "tags", "cell_details", "title_details":
			values, ok := value.([]any)
			limit := 30
			if key == "tags" {
				limit = 20
			}
			if !ok || len(values) > limit {
				return false
			}
			for _, value := range values {
				text, ok := value.(string)
				if !ok || utf8.RuneCountInString(text) > 500 {
					return false
				}
			}
		default:
			return false
		}
	}
	if !scheduleSource.MatchString(Text(row["source"])) {
		return false
	}
	start, end := Number(row["start_section"]), Number(row["end_section"])
	if start > 0 && end > 0 && start > end {
		return false
	}
	startTime, endTime := Text(row["start_time"]), Text(row["end_time"])
	if (startTime == "") != (endTime == "") {
		return false
	}
	return startTime == "" || selectionMinutes(startTime) >= 0 && selectionMinutes(endTime) >= 0
}

func NormalizeScheduleMeeting(row map[string]any, term, source string, week *int) SelectionMeeting {
	meeting := NormalizeSelectionMeeting(row, term, source)
	if len(meeting.Weeks) == 0 && week != nil {
		withWeek := map[string]any{}
		for key, value := range row {
			withWeek[key] = value
		}
		withWeek["weeks"] = []any{*week}
		meeting = NormalizeSelectionMeeting(withWeek, term, source)
	}
	return meeting
}

func CheckScheduleConflicts(baseline, candidates []SelectionMeeting, available, stale, ignoreSame bool) []map[string]any {
	results := []map[string]any{}
	for _, candidate := range candidates {
		status := "clear"
		if !available || len(candidate.Weeks) == 0 || candidate.Day == 0 || !selectionTimingKnown(candidate) {
			status = "unknown"
		}
		matches := []map[string]any{}
		if available {
			for _, row := range baseline {
				if ignoreSame && sameSelectionCourse(row, candidate) {
					continue
				}
				if match := compareSelectionMeetings(row, candidate, "personal_timetable"); match != nil {
					match["baseline_course_code"], match["baseline_teaching_class_id"], match["baseline_weeks"] = row.Code, row.Class, row.Weeks
					matches = append(matches, match)
					if match["status"] == "conflict" {
						status = "conflict"
					} else if status == "clear" {
						status = "unknown"
					}
				}
			}
		}
		if stale {
			status = "unknown"
		}
		results = append(results, map[string]any{"candidate_id": firstText(candidate.SourceID, candidate.ID), "candidate_meeting_id": candidate.ID, "status": status, "matches": matches})
	}
	return results
}
