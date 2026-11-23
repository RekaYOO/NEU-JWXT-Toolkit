package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"sort"
	"strconv"
)

func FetchPersonalTimetable(ctx context.Context, requester Requester, term string) (map[string]any, error) {
	query := TimetableContext{Mode: "personal", Term: term, Campus: "all"}
	result, err := FetchTimetableContext(ctx, requester, query)
	if err != nil {
		return nil, err
	}
	sections := map[string]any{"all": result["sections"]}
	for _, item := range result["campuses"].([]map[string]any) {
		code := Text(item["code"])
		if code == "all" {
			continue
		}
		body, err := timetablePost(ctx, requester, "kbapp/api/wdkbcx/getMySectionList.do", url.Values{"XNXQDM": {term}, "XQDM": {code}})
		if err != nil {
			return nil, err
		}
		rows, err := timetableRows(body, "")
		if err != nil {
			return nil, err
		}
		values := []map[string]any{}
		for index, row := range rows {
			number := int(Number(firstText(row["sectionNumber"], row["code"], row["JC"], row["DM"])))
			if number == 0 {
				number = index + 1
			}
			values = append(values, map[string]any{"number": number, "name": firstText(row["name"], row["MC"], "第"+strconv.Itoa(number)+"节"), "start_time": firstText(row["startTime"], row["KSSJ"]), "end_time": firstText(row["endTime"], row["JSSJ"])})
		}
		sections[code] = values
	}
	schedule, err := FetchTimetableSchedule(ctx, requester, TimetableSchedule{TimetableContext: query})
	if err != nil {
		return nil, err
	}
	return map[string]any{"term_code": term, "campuses": result["campuses"], "weeks": result["weeks"], "sections_by_campus": sections, "courses": schedule["courses"], "unscheduled": schedule["unscheduled"], "practices": schedule["practices"]}, nil
}

func TimetableSemantic(payload map[string]any) (json.RawMessage, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	var copy map[string]any
	if err := json.Unmarshal(encoded, &copy); err != nil {
		return nil, err
	}
	var normalize func(any)
	normalize = func(value any) {
		switch typed := value.(type) {
		case map[string]any:
			for key, nested := range typed {
				if key == "current" {
					continue
				}
				normalize(nested)
			}
		case []any:
			for _, nested := range typed {
				normalize(nested)
			}
			sort.SliceStable(typed, func(left, right int) bool {
				first, _ := json.Marshal(typed[left])
				second, _ := json.Marshal(typed[right])
				return string(first) < string(second)
			})
		}
	}
	if weeks, ok := copy["weeks"].([]any); ok {
		for _, raw := range weeks {
			if week := Object(raw); week != nil {
				delete(week, "current")
			}
		}
	}
	normalize(copy)
	return json.Marshal(copy)
}
