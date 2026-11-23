package academic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

const experimentBase = "https://jwxt.neu.edu.cn/jwapp/sys/syxkapp/api/"

type ExperimentQuery struct {
	Term    string `json:"term"`
	Task    string `json:"task_id"`
	Course  string `json:"course_no"`
	Project string `json:"project_code"`
}

func (query ExperimentQuery) Valid() bool {
	return timetableTermPattern.MatchString(query.Term) && ValidEvaluationID(query.Task) && ValidEvaluationID(query.Course) && ValidEvaluationID(query.Project)
}

func experimentData(ctx context.Context, requester Requester, group, operation string, form url.Values) (any, error) {
	encoded, err := requester.Request(ctx, http.MethodPost, experimentBase+group+"/"+operation+".do", form)
	if err != nil {
		return nil, err
	}
	payload, err := Decode(encoded)
	if err != nil {
		return nil, err
	}
	if Text(payload["code"]) != "0" {
		return nil, &campus.Error{Code: "ACADEMIC_RESPONSE_REJECTED", Message: "教务系统未确认实验选课查询成功"}
	}
	datas := Object(payload["datas"])
	value, exists := datas[operation]
	if !exists {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "实验选课响应缺少所需数据"}
	}
	return value, nil
}

func experimentRows(value any) ([]map[string]any, error) {
	if _, ok := value.([]any); !ok {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "实验选课列表结构异常"}
	}
	return reportRows(value)
}

func FetchExperimentTerm(ctx context.Context, requester Requester) (string, error) {
	value, err := experimentData(ctx, requester, "sypz", "queryAcademicYearSemester", url.Values{})
	if err != nil {
		return "", err
	}
	term := Text(value)
	if term != "" && !timetableTermPattern.MatchString(term) {
		return "", &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "实验选课学期格式异常"}
	}
	return term, nil
}

func experimentTrue(value any) bool {
	return value == true || Text(value) == "1" || strings.EqualFold(Text(value), "true")
}

func FetchExperimentCourses(ctx context.Context, requester Requester, term string) (map[string]any, error) {
	var err error
	if term == "" {
		term, err = FetchExperimentTerm(ctx, requester)
	}
	if err != nil {
		return nil, err
	}
	courses, results := []map[string]any{}, []map[string]any{}
	if term == "" {
		return map[string]any{"term": "", "courses": courses, "selected_results": results, "total": 0}, nil
	}
	if !timetableTermPattern.MatchString(term) {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "实验选课学期无效"}
	}
	value, err := experimentData(ctx, requester, "xsxk", "queryCanSelectedCourses", url.Values{"XNXQDM": {term}})
	if err != nil {
		return nil, err
	}
	rows, err := experimentRows(value)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		projects := []map[string]any{}
		selected := 0
		if raw := row["allExperimentProjectList"]; raw != nil {
			items, err := experimentRows(raw)
			if err != nil {
				return nil, err
			}
			for _, project := range items {
				round := Text(project["selectedRoundId"])
				if round != "" {
					selected++
				}
				projects = append(projects, map[string]any{"project_name": Text(project["projectName"]), "project_code": Text(project["projectCode"]), "course_no": firstText(project["courseNo"], row["courseNo"]), "must_do": experimentTrue(project["mustDo"]), "selected_round_id": round, "select_status": Text(project["selectStatus"]), "is_selected": round != ""})
			}
		}
		courses = append(courses, map[string]any{"task_id": Text(row["taskId"]), "course_name": Text(row["courseName"]), "course_no": Text(row["courseNo"]), "credit": Number(row["credit"]), "term_code": firstText(row["termCode"], term), "experiment_hours": Number(row["experimentalHours"]), "center_name": Text(row["experimentCenterName"]), "college_name": Text(row["openingCollegeName"]), "must_do_count": int(Number(row["mustDoProjectNums"])), "selected_count": selected, "is_complete": selected >= int(Number(row["mustDoProjectNums"])), "projects": projects})
	}
	results, err = FetchExperimentResults(ctx, requester, term)
	if err != nil {
		return nil, err
	}
	return map[string]any{"term": term, "courses": courses, "selected_results": results, "total": len(courses)}, nil
}

func FetchExperimentResults(ctx context.Context, requester Requester, term string) ([]map[string]any, error) {
	value, err := experimentData(ctx, requester, "xkjg", "queryStudentAllSelectCourseResult", url.Values{"XNXQDM": {term}})
	if err != nil {
		return nil, err
	}
	rows, err := experimentRows(value)
	if err != nil {
		return nil, err
	}
	results := []map[string]any{}
	for _, task := range rows {
		if task["projectResultList"] == nil {
			continue
		}
		items, err := experimentRows(task["projectResultList"])
		if err != nil {
			return nil, err
		}
		for _, item := range items {
			result := map[string]any{"task_id": Text(task["taskId"]), "course_name": firstText(item["courseName"], task["courseName"]), "course_no": firstText(item["courseNo"], task["courseNo"]), "project_name": Text(item["projectName"]), "project_code": Text(item["projectCode"]), "selected_round_id": Text(item["roundId"]), "round_name": Text(item["roundName"]), "select_status": Text(item["operationType"]), "must_do": Text(item["isMustDo"]) == "1", "term_code": firstText(item["termCode"], term)}
			for target, source := range experimentScheduleFields {
				result[target] = Text(item[source])
			}
			results = append(results, result)
		}
	}
	return results, nil
}

var experimentScheduleFields = map[string]string{"teacher": "classTeachers", "week": "classWeeks", "day": "classDays", "time": "classSessions", "location": "classrooms", "select_start": "selectCourseStartDate", "select_end": "selectCourseEndDate"}

func FetchExperimentRounds(ctx context.Context, requester Requester, query ExperimentQuery) ([]map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "实验班查询参数无效"}
	}
	value, err := experimentData(ctx, requester, "xsxk", "queryTaskProjectRounds", url.Values{"XNXQDM": {query.Term}, "RWID": {query.Task}, "SYRW_WID": {query.Task}, "KCH": {query.Course}, "SYXMDM": {query.Project}})
	if err != nil {
		return nil, err
	}
	rows, err := experimentRows(value)
	if err != nil {
		return nil, err
	}
	result := []map[string]any{}
	for _, row := range rows {
		round := map[string]any{"wid": Text(row["wid"]), "round_name": Text(row["roundName"]), "selected_count": Number(row["selectedNums"]), "capacity": Number(row["courseCapacity"]), "conflict": experimentTrue(row["conflict"]), "selected": experimentTrue(row["selected"])}
		for target, source := range experimentScheduleFields {
			round[target] = Text(row[source])
		}
		result = append(result, round)
	}
	return result, nil
}

func ExperimentRoundAvailability(round map[string]any, query ExperimentQuery, baseline []SelectionMeeting, available, stale bool, now time.Time) map[string]any {
	candidateRow := map[string]any{}
	for key, value := range round {
		candidateRow[key] = value
	}
	candidateRow["candidate_id"], candidateRow["course_name"], candidateRow["course_code"] = round["wid"], firstText(round["round_name"], query.Project), query.Course
	candidate := NormalizeSelectionMeeting(candidateRow, query.Term, "experiment")
	status := "clear"
	if !available || stale || len(candidate.Weeks) == 0 || candidate.Day < 1 || !selectionTimingKnown(candidate) {
		status = "unknown"
	}
	conflicts := []map[string]any{}
	if available {
		for _, meeting := range baseline {
			if match := compareSelectionMeetings(meeting, candidate, "personal_timetable"); match != nil {
				if !stale && match["status"] == "conflict" {
					status = "conflict"
				} else if status == "clear" {
					status = "unknown"
				}
				conflicts = append(conflicts, map[string]any{"meeting_id": match["baseline_meeting_id"], "course_name": match["baseline_course_name"], "reason": match["reason"], "overlapping_weeks": match["overlapping_weeks"], "weekday": match["weekday"], "start_section": max(meeting.Start, candidate.Start), "end_section": min(meeting.End, candidate.End)})
			}
		}
	}
	window := "unknown"
	start, startErr := experimentTime(Text(round["select_start"]))
	end, endErr := experimentTime(Text(round["select_end"]))
	if startErr == nil && endErr == nil && !start.IsZero() && !end.IsZero() && !end.Before(start) {
		window = "open"
		if now.Before(start) {
			window = "not_started"
		} else if now.After(end) {
			window = "ended"
		}
	}
	full := Number(round["selected_count"]) >= Number(round["capacity"])
	reason, source := "", "personal_timetable"
	if full {
		reason = "实验班已满"
	} else if round["conflict"] == true {
		reason = "教务系统判定时间冲突"
	} else if status == "conflict" {
		reason = "与个人课表冲突"
	} else if window == "not_started" {
		reason = "尚未到选课时间"
	} else if window == "ended" {
		reason = "选课时间已结束"
	}
	if round["conflict"] == true {
		status, source = "conflict", "official"
	}
	round["is_full"], round["can_select"], round["disabled_reason"], round["selection_window_state"] = full, reason == "", reason, window
	round["conflict_status"], round["conflict_source"], round["conflicts"] = status, source, conflicts
	round["baseline_available"], round["baseline_stale"] = available, stale || !available
	if candidate.Weeks == nil {
		candidate.Weeks = []int{}
	}
	round["weeks"], round["weekday"], round["start_section"], round["end_section"] = candidate.Weeks, candidate.Day, candidate.Start, candidate.End
	return round
}

func experimentTime(value string) (time.Time, error) {
	value = strings.ReplaceAll(strings.TrimSpace(value), "/", "-")
	if parsed, err := time.Parse(time.RFC3339Nano, value); err == nil {
		return parsed, nil
	}
	zone := time.FixedZone("Asia/Shanghai", 8*3600)
	for _, layout := range []string{"2006-01-02", "2006-01-02 15:04", "2006-01-02 15:04:05", "2006-01-02T15:04", "2006-01-02T15:04:05"} {
		if parsed, err := time.ParseInLocation(layout, value, zone); err == nil {
			return parsed, nil
		}
	}
	return ParseJWXKTaskTime(value)
}

func ExperimentSemantic(payload map[string]any) json.RawMessage {
	encoded, _ := json.Marshal(payload)
	var normalized map[string]any
	json.Unmarshal(encoded, &normalized)
	for _, name := range []string{"courses", "selected_results"} {
		rows, _ := reportRows(normalized[name])
		sort.SliceStable(rows, func(left, right int) bool {
			first, second := rows[left], rows[right]
			return Text(first["task_id"])+"\x00"+Text(first["project_code"])+"\x00"+Text(first["selected_round_id"]) < Text(second["task_id"])+"\x00"+Text(second["project_code"])+"\x00"+Text(second["selected_round_id"])
		})
		normalized[name] = rows
	}
	encoded, _ = json.Marshal(normalized)
	return encoded
}

func ExperimentDiff(previous, current json.RawMessage) map[string]any {
	oldPayload, newPayload := map[string]any{}, map[string]any{}
	json.Unmarshal(previous, &oldPayload)
	json.Unmarshal(current, &newPayload)
	json.Unmarshal(ExperimentSemantic(oldPayload), &oldPayload)
	json.Unmarshal(ExperimentSemantic(newPayload), &newPayload)
	index := func(payload map[string]any) map[string]map[string]any {
		result := map[string]map[string]any{}
		rows, _ := reportRows(payload["courses"])
		for _, row := range rows {
			result[firstText(row["task_id"], row["course_no"])] = row
		}
		return result
	}
	oldCourses, newCourses := index(oldPayload), index(newPayload)
	added, removed, changed := []string{}, []string{}, []string{}
	for key, course := range newCourses {
		if previous, exists := oldCourses[key]; !exists {
			added = append(added, key)
		} else if !reflect.DeepEqual(previous, course) {
			changed = append(changed, key)
		}
	}
	for key := range oldCourses {
		if _, exists := newCourses[key]; !exists {
			removed = append(removed, key)
		}
	}
	sort.Strings(added)
	sort.Strings(removed)
	sort.Strings(changed)
	return map[string]any{"term_changed": Text(oldPayload["term"]) != Text(newPayload["term"]), "added_course_ids": added, "removed_course_ids": removed, "changed_course_ids": changed, "selected_results_changed": !reflect.DeepEqual(oldPayload["selected_results"], newPayload["selected_results"])}
}
