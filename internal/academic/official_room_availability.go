package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type OfficialRoomQuery struct {
	Term    string         `json:"term_code"`
	Date    string         `json:"date"`
	Start   int            `json:"start_section"`
	End     int            `json:"end_section"`
	Keyword string         `json:"keyword"`
	Filters map[string]any `json:"filters"`
}

func (query OfficialRoomQuery) catalogQuery() TimetableTargetQuery {
	return TimetableTargetQuery{Mode: "room", Term: query.Term, Keyword: query.Keyword, Filters: query.Filters, Page: 1, PageSize: 50}
}

func (query OfficialRoomQuery) Valid() bool {
	date, err := time.Parse(time.DateOnly, query.Date)
	return err == nil && date.Year() >= 1900 && date.Format(time.DateOnly) == query.Date &&
		query.Start >= 1 && query.Start <= query.End && query.End <= 12 && query.catalogQuery().Valid()
}

var officialRoomFilters = []struct{ key, field string }{
	{"campus", "XXXQDM"}, {"building", "JXLDM"}, {"room_type", "JASLXDM"},
}

func (query OfficialRoomQuery) form() (url.Values, bool) {
	keyword := strings.TrimSpace(query.Keyword)
	needsCatalog := keyword != "" && targetCodePattern.MatchString(keyword)
	form := url.Values{"KXRQ": {query.Date}, "KSJC": {strconv.Itoa(query.Start)}, "JSJC": {strconv.Itoa(query.End)}, "pageSize": {"15"}}
	rules := []map[string]string{}
	for _, field := range []string{"KXRQ", "KSJC", "JSJC"} {
		rules = append(rules, map[string]string{"name": field, "value": form.Get(field), "builder": "equal", "linkOpt": "AND"})
	}
	for _, filter := range officialRoomFilters {
		if value := Text(query.Filters[filter.key]); value != "" {
			form.Set(filter.field, value)
			rules = append(rules, map[string]string{"name": filter.field, "value": value, "builder": "equal", "linkOpt": "AND"})
		}
	}
	for key, value := range query.Filters {
		if key != "campus" && key != "building" && key != "room_type" && Text(value) != "" {
			needsCatalog = true
		}
	}
	if keyword != "" && !targetCodePattern.MatchString(keyword) {
		form.Set("JASMC", keyword)
		rules = append(rules, map[string]string{"name": "JASMC", "value": keyword, "builder": "include", "linkOpt": "AND"})
	}
	encoded, _ := json.Marshal(rules)
	form.Set("querySetting", string(encoded))
	return form, needsCatalog
}

func invalidOfficialRooms() error {
	return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校空闲教室结果不完整或格式异常"}
}

func FetchOfficialAvailableRooms(ctx context.Context, requester Requester, query OfficialRoomQuery) ([]map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "空闲教室查询条件无效"}
	}
	form, needsCatalog := query.form()
	items := []map[string]any{}
	seen := map[string]bool{}
	count, expectedTotal, expectedPageSize := 0, -1, 0
	for page := 1; page <= 400; page++ {
		form.Set("pageNumber", strconv.Itoa(page))
		payload, err := timetablePost(ctx, requester, "kxjas/modules/kxjscxxs/cxkxjs.do", form)
		if err != nil {
			return nil, err
		}
		root := Object(payload)
		table := Object(Object(root["datas"])["cxkxjs"])
		total, totalErr := strconv.Atoi(Text(table["totalSize"]))
		pageSize, sizeErr := strconv.Atoi(Text(table["pageSize"]))
		value, hasRows := table["rows"]
		rows, rowsErr := reportRows(value)
		if Text(root["code"]) != "0" || !hasRows || rowsErr != nil || totalErr != nil || total < 0 || total > 5000 ||
			(total > 0 && (sizeErr != nil || pageSize < 1)) || (page > 1 && (total != expectedTotal || pageSize != expectedPageSize)) ||
			count+len(rows) > total || (count+len(rows) < total && len(rows) != pageSize) {
			return nil, invalidOfficialRooms()
		}
		expectedTotal, expectedPageSize = total, pageSize
		for _, row := range rows {
			id, name := Text(row["JASDM"]), Text(row["JASMC"])
			section, clock := Text(row["KXJC"]), Text(row["KXSJ"])
			if id == "" || len(id) > 128 || seen[id] || name == "" || Text(row["KXRQ"]) != query.Date || section == "" || clock == "" {
				return nil, invalidOfficialRooms()
			}
			seen[id] = true
			for _, filter := range officialRoomFilters {
				if requested := form.Get(filter.field); requested != "" && requested != Text(row[filter.field]) {
					return nil, invalidOfficialRooms()
				}
			}
			if keyword := form.Get("JASMC"); keyword != "" && !strings.Contains(strings.ToLower(name), strings.ToLower(keyword)) {
				return nil, invalidOfficialRooms()
			}
			items = append(items, map[string]any{
				"id": id, "name": name,
				"details":       map[string]string{"campus": Text(row["XXXQDM_DISPLAY"]), "building": Text(row["JXLDM_DISPLAY"]), "type": Text(row["JASLXDM_DISPLAY"])},
				"filter_values": map[string]string{"campus": Text(row["XXXQDM"])},
				"availability":  map[string]string{"date": query.Date, "sections": section, "time": clock},
			})
		}
		count += len(rows)
		if count == total {
			if needsCatalog && len(items) > 0 {
				return filterOfficialRooms(ctx, requester, query.catalogQuery(), items)
			}
			return items, nil
		}
	}
	return nil, invalidOfficialRooms()
}

func filterOfficialRooms(ctx context.Context, requester Requester, query TimetableTargetQuery, rooms []map[string]any) ([]map[string]any, error) {
	matched := map[string]map[string]any{}
	remaining := map[string]bool{}
	for _, room := range rooms {
		remaining[Text(room["id"])] = true
	}
	query.PageSize = 20
	for ; query.Page <= 1000 && len(remaining) > 0; query.Page++ {
		payload, err := SearchTimetableTargets(ctx, requester, query)
		if err != nil {
			return nil, err
		}
		for _, item := range payload["items"].([]map[string]any) {
			id := Text(item["id"])
			if remaining[id] {
				if roomMatchesFilters(item, query.Filters) {
					matched[id] = item
				}
				delete(remaining, id)
			}
		}
		total, size := payload["total"].(int), payload["page_size"].(int)
		if total > 20000 || size != query.PageSize {
			return nil, invalidOfficialRooms()
		}
		if query.Page*size >= total {
			break
		}
	}
	filtered := []map[string]any{}
	for _, room := range rooms {
		if match := matched[Text(room["id"])]; match != nil {
			match["availability"] = room["availability"]
			filtered = append(filtered, match)
		}
	}
	return filtered, nil
}
