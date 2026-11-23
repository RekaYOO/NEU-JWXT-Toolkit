package academic

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
)

type SelectionMeeting struct {
	Class                          string
	ID, SourceID, Term, Code, Name string
	Weeks                          []int
	Day, Start, End                int
	StartTime, EndTime             string
}

var selectionClockRange = regexp.MustCompile(`(\d{1,2}:\d{2})\s*[-~～—至]\s*(\d{1,2}:\d{2})`)

func NormalizeSelectionMeeting(row map[string]any, term, source string) SelectionMeeting {
	meeting := SelectionMeeting{ID: Text(row["meeting_id"]), SourceID: firstText(row["source_id"], row["candidate_id"], row["teaching_class_id"], row["teachingClassId"], row["id"]), Term: term, Code: firstText(row["course_code"], row["courseCode"]), Name: firstText(row["course_name"], row["courseName"], "未命名课程")}
	meeting.Class = firstText(row["teaching_class_id"], row["teachingClassId"])
	for _, key := range []string{"weeks", "week_list", "weekList", "week_text", "weekText", "classWeeks", "week"} {
		value := row[key]
		if typed, ok := value.([]int); ok {
			converted := []any{}
			for _, week := range typed {
				converted = append(converted, week)
			}
			value = converted
		}
		if value != nil && Text(value) != "" && Text(value) != "[]" {
			meeting.Weeks = ParseWeeks(value, false)
			break
		}
	}
	if len(meeting.Weeks) == 0 {
		for _, key := range []string{"weeks_and_teachers", "weeksAndTeachers", "cell_details", "cellDetail", "title_details", "titleDetail"} {
			if weeks := ParseWeeks(row[key], true); len(weeks) > 0 {
				meeting.Weeks = weeks
				break
			}
		}
	}
	day := firstText(row["weekday"], row["dayOfWeek"], row["day"], row["classDays"])
	meeting.Day = int(Number(day))
	if meeting.Day < 1 || meeting.Day > 7 {
		meeting.Day = jwxkDay(day)
	}
	meeting.Start, meeting.End = int(Number(firstText(row["start_section"], row["beginSection"]))), int(Number(firstText(row["end_section"], row["endSection"])))
	timing := firstText(row["time"], row["classSessions"])
	if meeting.Start == 0 && meeting.End == 0 {
		meeting.Start, meeting.End = jwxkSections(timing)
	}
	meeting.StartTime, meeting.EndTime = firstText(row["start_time"], row["beginTime"]), firstText(row["end_time"], row["endTime"])
	if meeting.StartTime == "" && meeting.EndTime == "" {
		if match := selectionClockRange.FindStringSubmatch(timing); len(match) > 0 {
			meeting.StartTime, meeting.EndTime = match[1], match[2]
		}
	}
	if meeting.ID == "" {
		shape := map[string]any{"source": firstText(row["source"], source), "source_id": meeting.SourceID, "term_code": term, "course_name": meeting.Name, "course_code": meeting.Code, "teaching_class_id": firstText(row["teaching_class_id"], row["teachingClassId"]), "weeks": meeting.Weeks, "weekday": meeting.Day, "start_section": meeting.Start, "end_section": meeting.End, "start_time": meeting.StartTime, "end_time": meeting.EndTime, "location": firstText(row["location"], row["place"]), "campus": firstText(row["campus"], row["campusName"])}
		if meeting.Weeks == nil {
			shape["weeks"] = []int{}
		}
		encoded, _ := json.Marshal(shape)
		digest := sha256.Sum256(encoded)
		meeting.ID = "mtg_" + hex.EncodeToString(digest[:])[:24]
	}
	return meeting
}

func selectionMinutes(value string) int {
	parts := strings.Split(strings.TrimSpace(value), ":")
	if len(parts) != 2 || len(parts[0]) < 1 || len(parts[0]) > 2 || len(parts[1]) != 2 {
		return -1
	}
	hour, hourErr := strconv.Atoi(parts[0])
	minute, minuteErr := strconv.Atoi(parts[1])
	if hourErr != nil || minuteErr != nil || hour < 0 || hour > 23 || minute < 0 || minute > 59 {
		return -1
	}
	return hour*60 + minute
}

func selectionTimingKnown(meeting SelectionMeeting) bool {
	if meeting.Start >= 1 && meeting.End >= meeting.Start && meeting.End <= 30 {
		return true
	}
	start, end := selectionMinutes(meeting.StartTime), selectionMinutes(meeting.EndTime)
	return start >= 0 && end > start
}

func sameSelectionCourse(left, right SelectionMeeting) bool {
	if left.Code != "" && right.Code != "" {
		return strings.EqualFold(left.Code, right.Code)
	}
	normalize := func(value string) string { return strings.ToLower(strings.Join(strings.Fields(value), "")) }
	return normalize(left.Name) != "" && normalize(left.Name) == normalize(right.Name)
}

func compareSelectionMeetings(baseline, candidate SelectionMeeting, source string) map[string]any {
	if baseline.Term != candidate.Term {
		return nil
	}
	weeks := []int{}
	unknown := ""
	if len(baseline.Weeks) > 0 && len(candidate.Weeks) > 0 {
		for _, week := range baseline.Weeks {
			for _, other := range candidate.Weeks {
				if week == other {
					weeks = append(weeks, week)
					break
				}
			}
		}
		if len(weeks) == 0 {
			return nil
		}
	} else {
		unknown = "unknown_weeks"
	}
	if baseline.Day > 0 && candidate.Day > 0 {
		if baseline.Day != candidate.Day {
			return nil
		}
	} else if unknown == "" {
		unknown = "unknown_weekday"
	}
	reason := "section_overlap"
	if baseline.Start >= 1 && baseline.End >= baseline.Start && baseline.End <= 30 && candidate.Start >= 1 && candidate.End >= candidate.Start && candidate.End <= 30 {
		if baseline.End < candidate.Start || candidate.End < baseline.Start {
			return nil
		}
	} else {
		baselineStart, baselineEnd := selectionMinutes(baseline.StartTime), selectionMinutes(baseline.EndTime)
		candidateStart, candidateEnd := selectionMinutes(candidate.StartTime), selectionMinutes(candidate.EndTime)
		if baselineStart < 0 || baselineEnd <= baselineStart || candidateStart < 0 || candidateEnd <= candidateStart {
			if unknown == "" {
				unknown = "unknown_time"
			}
		} else if baselineEnd <= candidateStart || candidateEnd <= baselineStart {
			return nil
		} else {
			reason = "time_overlap"
		}
	}
	status := "conflict"
	if unknown != "" {
		status, reason = "unknown", unknown
	}
	day := baseline.Day
	if day == 0 {
		day = candidate.Day
	}
	return map[string]any{"baseline_meeting_id": baseline.ID, "baseline_course_name": baseline.Name, "status": status, "reason": reason, "source": source, "overlapping_weeks": weeks, "weekday": day, "start_section": baseline.Start, "end_section": baseline.End}
}

func PreviewSelectionConflicts(term string, baseline, candidates []SelectionMeeting, available, stale bool) map[string]any {
	results := []map[string]any{}
	for index, candidate := range candidates {
		status := "clear"
		if !available || len(candidate.Weeks) == 0 || candidate.Day == 0 || !selectionTimingKnown(candidate) {
			status = "unknown"
		}
		matches := []map[string]any{}
		for _, source := range []string{"personal_timetable", "selection_plan"} {
			rows := baseline
			if source == "selection_plan" {
				rows = candidates
			} else if !available {
				continue
			}
			for position, row := range rows {
				if source == "selection_plan" && index == position || sameSelectionCourse(row, candidate) {
					continue
				}
				if match := compareSelectionMeetings(row, candidate, source); match != nil {
					matches = append(matches, match)
					if match["status"] == "conflict" {
						status = "conflict"
					} else if status == "clear" {
						status = "unknown"
					}
				}
			}
		}
		results = append(results, map[string]any{"candidate_id": firstText(candidate.SourceID, candidate.ID), "candidate_meeting_id": candidate.ID, "status": status, "matches": matches})
	}
	return map[string]any{"term_code": term, "baseline_available": available, "baseline_stale": stale, "results": results}
}
