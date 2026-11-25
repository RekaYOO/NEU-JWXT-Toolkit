package academic

import (
	"context"
	"math"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func roomFilterNumber(value string) (float64, bool) {
	number, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
	return number, err == nil && !math.IsNaN(number) && !math.IsInf(number, 0)
}

func roomMatchesFilters(room map[string]any, filters map[string]any) bool {
	values, _ := room["filter_values"].(map[string]string)
	details, _ := room["details"].(map[string]string)
	for key, value := range filters {
		wanted := Text(value)
		if wanted == "" {
			continue
		}
		switch key {
		case "min_capacity", "max_capacity":
			capacity, known := roomFilterNumber(details["capacity"])
			limit, valid := roomFilterNumber(wanted)
			if !known || capacity < 0 || !valid || (key == "min_capacity" && capacity < limit) || (key == "max_capacity" && capacity > limit) {
				return false
			}
		case "floor":
			floor := values[key]
			if floor == wanted {
				continue
			}
			actual, known := roomFilterNumber(strings.TrimSuffix(floor, "层"))
			expected, valid := roomFilterNumber(strings.TrimSuffix(wanted, "层"))
			if !known || !valid || actual != expected {
				return false
			}
		case "has_schedule":
			status := values[key]
			if status == "" {
				status = Text(room[key])
			}
			switch strings.ToLower(status) {
			case "1", "yes", "true", "是", "已排课", "已有排课":
				status = "yes"
			case "0", "no", "false", "否", "未排课", "尚无排课":
				status = "no"
			}
			if status != wanted {
				return false
			}
		default:
			if values[key] != wanted {
				return false
			}
		}
	}
	return true
}

type RoomSlot struct {
	WeekStart *int   `json:"week_start"`
	WeekEnd   *int   `json:"week_end"`
	DayStart  *int   `json:"weekday_start"`
	DayEnd    *int   `json:"weekday_end"`
	Start     *int   `json:"start_section"`
	End       *int   `json:"end_section"`
	Week      *int   `json:"week"`
	Day       *int   `json:"weekday"`
	Joiner    string `json:"joiner"`
}

func (slot *RoomSlot) normalize() bool {
	for _, bound := range []*int{slot.WeekStart, slot.WeekEnd, slot.Start, slot.End, slot.Week} {
		if bound != nil && (*bound < 1 || *bound > 30) {
			return false
		}
	}
	for _, bound := range []*int{slot.DayStart, slot.DayEnd, slot.Day} {
		if bound != nil && (*bound < 1 || *bound > 7) {
			return false
		}
	}
	if slot.Week != nil {
		if slot.WeekStart == nil {
			slot.WeekStart = slot.Week
		}
		if slot.WeekEnd == nil {
			slot.WeekEnd = slot.Week
		}
	}
	if slot.Day != nil {
		if slot.DayStart == nil {
			slot.DayStart = slot.Day
		}
		if slot.DayEnd == nil {
			slot.DayEnd = slot.Day
		}
	}
	if slot.Joiner == "" {
		slot.Joiner = "and"
	}
	if slot.Joiner != "and" && slot.Joiner != "or" {
		return false
	}
	if slot.WeekStart != nil && slot.WeekEnd != nil && *slot.WeekStart > *slot.WeekEnd {
		return false
	}
	if slot.Start != nil && slot.End != nil && *slot.Start > *slot.End {
		return false
	}
	return slot.DayStart == nil || slot.DayEnd == nil || *slot.DayStart%7 <= *slot.DayEnd%7
}

func RoomFree(courses []map[string]any, slots []RoomSlot) bool {
	combined := true
	for index, slot := range slots {
		if !slot.normalize() {
			return false
		}
		free := true
		start, end := 1, 30
		if slot.Start != nil {
			start = *slot.Start
		}
		if slot.End != nil {
			end = *slot.End
		}
		for _, course := range courses {
			day, first, last := int(Number(course["weekday"])), int(Number(course["start_section"])), int(Number(course["end_section"]))
			if day < 1 || day > 7 || first < 1 || last < first || last > 30 {
				free = false
				break
			}
			if slot.DayStart != nil && day%7 < *slot.DayStart%7 || slot.DayEnd != nil && day%7 > *slot.DayEnd%7 {
				continue
			}
			weeks, ok := course["weeks"].([]int)
			if !ok || len(weeks) == 0 {
				free = false
				break
			}
			matching := false
			for _, week := range weeks {
				if (slot.WeekStart == nil || week >= *slot.WeekStart) && (slot.WeekEnd == nil || week <= *slot.WeekEnd) {
					matching = true
				}
			}
			if matching && first <= end && last >= start {
				free = false
				break
			}
		}
		if index == 0 {
			combined = free
		} else if slot.Joiner == "or" {
			combined = combined || free
		} else {
			combined = combined && free
		}
	}
	return combined
}

type RoomAvailabilityQuery struct {
	Term    string         `json:"term_code"`
	Campus  string         `json:"campus_code"`
	Filters map[string]any `json:"filters"`
	Slots   []RoomSlot     `json:"slots"`
	Keyword string         `json:"keyword"`
	Cursor  int            `json:"cursor"`
	Limit   int            `json:"scan_limit"`
	Seen    []string       `json:"seen_room_ids"`
}

func (query *RoomAvailabilityQuery) Valid() bool {
	if !(TimetableTargetQuery{Mode: "room", Term: query.Term, Keyword: query.Keyword, Page: 1, PageSize: 20, Filters: query.Filters}).Valid() || query.Campus == "" || len(query.Campus) > 64 || query.Cursor < 0 || query.Cursor > 20000 || query.Limit < 1 || query.Limit > 10 || len(query.Seen) > 20000 || len(query.Slots) < 1 || len(query.Slots) > 20 {
		return false
	}
	for index := range query.Slots {
		if !query.Slots[index].normalize() {
			return false
		}
	}
	for index, identifier := range query.Seen {
		query.Seen[index] = strings.TrimSpace(identifier)
		if query.Seen[index] == "" || len(query.Seen[index]) > 128 {
			return false
		}
	}
	return true
}

func ScanAvailableRooms(ctx context.Context, requester Requester, query RoomAvailabilityQuery) (map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "空闲教室查询条件无效"}
	}
	seen := map[string]bool{}
	for _, id := range query.Seen {
		seen[id] = true
	}
	items, scannedIDs := []map[string]any{}, []string{}
	total, cursor := 0, query.Cursor
	exhausted := false
	startPage, startOffset := query.Cursor/20+1, query.Cursor%20
	scan := func(firstPage, lastPage, offset int) (bool, error) {
		for page := firstPage; lastPage == 0 || page <= lastPage; page++ {
			if page > 1000 {
				return false, &campus.Error{Code: "TIMETABLE_CATALOG_LIMIT", Message: "教室目录超过扫描上限，请缩小筛选范围"}
			}
			payload, err := SearchTimetableTargets(ctx, requester, TimetableTargetQuery{Mode: "room", Term: query.Term, Keyword: query.Keyword, Page: page, PageSize: 20, Filters: query.Filters})
			if err != nil {
				return false, err
			}
			rooms := payload["items"].([]map[string]any)
			total = payload["total"].(int)
			if total > 20000 {
				return false, &campus.Error{Code: "TIMETABLE_CATALOG_LIMIT", Message: "教室目录超过扫描上限，请缩小筛选范围"}
			}
			if payload["page_size"].(int) != 20 {
				return false, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "教室分页大小发生变化，请重新查询"}
			}
			pageCount := (total + 19) / 20
			if pageCount < 1 {
				pageCount = 1
			}
			if page > pageCount || len(rooms) == 0 {
				exhausted = true
				return false, nil
			}
			first := 0
			if page == firstPage {
				first = offset
			}
			for index := first; index < len(rooms); index++ {
				room := rooms[index]
				id := Text(room["id"])
				if id == "" || seen[id] || !roomMatchesFilters(room, query.Filters) {
					continue
				}
				schedule, err := FetchTimetableSchedule(ctx, requester, TimetableSchedule{TimetableContext: TimetableContext{Mode: "room", Term: query.Term, Campus: query.Campus, Target: id}})
				if err != nil {
					return false, err
				}
				seen[id] = true
				scannedIDs = append(scannedIDs, id)
				cursor = (page-1)*20 + index + 1
				if RoomFree(schedule["courses"].([]map[string]any), query.Slots) {
					items = append(items, room)
				}
				if len(scannedIDs) >= query.Limit {
					return true, nil
				}
			}
			if page >= pageCount {
				exhausted = true
				return false, nil
			}
		}
		return false, nil
	}
	reached, err := scan(startPage, 0, startOffset)
	if err != nil {
		return nil, err
	}
	if !reached && (startPage > 1 || startOffset > 0) && len(seen) < total {
		exhausted = false
		reached, err = scan(1, startPage, 0)
		if err != nil {
			return nil, err
		}
	}
	complete := total == 0 || len(seen) >= total || (len(query.Seen) == 0 && cursor >= total) || (!reached && exhausted)
	return map[string]any{"items": items, "total": len(items), "scanned": len(scannedIDs), "cursor": cursor, "candidate_total": total, "complete": complete, "scanned_room_ids": scannedIDs}, nil
}
