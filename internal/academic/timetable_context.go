package academic

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

var timetableTermPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)
var timetableModes = map[string]string{"personal": "", "class": "05", "teacher": "02", "room": "01"}

type TimetableContext struct {
	Mode   string `json:"mode"`
	Term   string `json:"term_code"`
	Target string `json:"target_id"`
	Campus string `json:"campus_code"`
}

func (query TimetableContext) Valid() bool {
	_, mode := timetableModes[query.Mode]
	return mode && timetableTermPattern.MatchString(query.Term) && len(query.Target) <= 128 && len(query.Campus) <= 64 && (query.Mode == "personal" || query.Target != "")
}

func timetablePost(ctx context.Context, requester Requester, path string, form url.Values) (any, error) {
	encoded, err := requester.Request(ctx, http.MethodPost, "https://jwxt.neu.edu.cn/jwapp/sys/"+path, form)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	var payload any
	if err := decoder.Decode(&payload); err != nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表响应不是有效数据"}
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表响应包含多余数据"}
	}
	if object := Object(payload); object != nil {
		if code, exists := object["code"]; exists && code != nil && Text(code) != "0" {
			return nil, &campus.Error{Code: "TIMETABLE_REJECTED", Message: "学校未确认课表查询成功"}
		}
	}
	return payload, nil
}

func timetableRows(payload any, preferred string) ([]map[string]any, error) {
	if _, ok := payload.([]any); ok {
		return reportRows(payload)
	}
	object := Object(payload)
	if object == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表列表结构异常"}
	}
	data, exists := object["datas"]
	if !exists {
		data = object
	}
	candidates := []any{}
	if nested := Object(data); nested != nil {
		if preferred != "" {
			candidates = append(candidates, nested[preferred])
		}
		candidates = append(candidates, data)
		keys := []string{}
		for key := range nested {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			candidates = append(candidates, nested[key])
		}
	} else {
		candidates = append(candidates, data)
	}
	for _, candidate := range candidates {
		if _, ok := candidate.([]any); ok {
			return reportRows(candidate)
		}
		if nested := Object(candidate); nested != nil {
			if rows, ok := nested["rows"]; ok {
				return reportRows(rows)
			}
		}
	}
	return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表响应缺少列表"}
}

func FetchTimetableTerms(ctx context.Context, requester Requester) (map[string]any, error) {
	currentBody, err := timetablePost(ctx, requester, "jwpubapp/modules/gg/cxmrxnxq.do", url.Values{"CSDM": {"SYS"}, "ZCSDM": {"DQXNXQDM"}})
	if err != nil {
		return nil, err
	}
	current := ""
	if rows, err := timetableRows(currentBody, "cxmrxnxq"); err == nil && len(rows) > 0 {
		current = firstText(rows[0]["XNXQDM"], rows[0]["DM"], rows[0]["CSZA"])
	}
	if current == "" {
		container := Object(currentBody)
		if data := Object(container["datas"]); data != nil {
			container = data
		}
		preferred := container["cxmrxnxq"]
		if value, ok := preferred.(string); ok {
			current = value
		} else if data := Object(preferred); data != nil {
			current = firstText(data["XNXQDM"], data["DM"], data["CSZA"], data["value"])
		}
		if current == "" {
			current = firstText(container["XNXQDM"], container["DQXNXQDM"], container["currentTerm"], container["value"])
		}
	}
	body, err := timetablePost(ctx, requester, "jwpubapp/modules/zdgl/xnxqcx.do", url.Values{"SFSY": {"1"}, "*order": {"+PX,-DM"}, "pageSize": {"200"}, "pageNumber": {"1"}})
	if err != nil {
		return nil, err
	}
	rows, err := timetableRows(body, "xnxqcx")
	if err != nil {
		return nil, err
	}
	terms := []map[string]any{}
	for _, row := range rows {
		code := firstText(row["DM"], row["XNXQDM"])
		if code != "" {
			terms = append(terms, map[string]any{"code": code, "name": firstText(row["MC"], row["XNXQMC"], code), "current": code == current})
		}
	}
	if len(terms) == 0 && current != "" {
		terms = append(terms, map[string]any{"code": current, "name": current, "current": true})
	}
	if len(terms) == 0 {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "教务系统未返回可用学期"}
	}
	return map[string]any{"terms": terms, "current": current}, nil
}

func shortDate(value string) string {
	if len(value) > 10 {
		return value[:10]
	}
	return value
}

func FetchTimetableContext(ctx context.Context, requester Requester, query TimetableContext) (map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "课表查询条件无效"}
	}
	form := url.Values{"XNXQDM": {query.Term}}
	path := "kbapp/api/wdkbcx/getMyScheduledCampus.do"
	if query.Mode != "personal" {
		path = "kbapp/api/qxkbcx/getMyScheduledCampus.do"
		form.Set("CODE", query.Target)
		form.Set("KBLX", timetableModes[query.Mode])
	}
	body, err := timetablePost(ctx, requester, path, form)
	if err != nil {
		return nil, err
	}
	rows, err := timetableRows(body, "")
	if err != nil {
		return nil, err
	}
	campuses := []map[string]any{}
	hasAll := false
	for _, row := range rows {
		code := firstText(row["id"], row["DM"], row["XQDM"])
		if code != "" {
			campuses = append(campuses, map[string]any{"code": code, "name": firstText(row["name"], row["MC"], row["XQMC"], code)})
			hasAll = hasAll || code == "all"
		}
	}
	if (query.Mode == "personal" || len(campuses) > 1) && !hasAll {
		campuses = append([]map[string]any{{"code": "all", "name": "全部校区"}}, campuses...)
	}
	campus := ""
	for _, item := range campuses {
		if item["code"] == query.Campus {
			campus = query.Campus
		}
	}
	if campus == "" && len(campuses) > 0 {
		campus = Text(campuses[0]["code"])
	}
	sections := []map[string]any{}
	if campus != "" {
		if campus == "all" {
			campus = ""
		}
		form = url.Values{"XNXQDM": {query.Term}, "XQDM": {campus}}
		path = "kbapp/api/wdkbcx/getMySectionList.do"
		if query.Mode != "personal" {
			path = "kbbpapp/api/kbck/getSectionList.do"
			form.Set("KBLX", timetableModes[query.Mode])
		}
		body, err = timetablePost(ctx, requester, path, form)
		if err != nil {
			return nil, err
		}
		rows, err = timetableRows(body, "")
		if err != nil {
			return nil, err
		}
		for index, row := range rows {
			number := int(Number(firstText(row["sectionNumber"], row["code"], row["JC"], row["DM"])))
			if number == 0 {
				number = index + 1
			}
			sections = append(sections, map[string]any{"number": number, "name": firstText(row["name"], row["MC"], "第"+strconv.Itoa(number)+"节"), "start_time": firstText(row["startTime"], row["KSSJ"]), "end_time": firstText(row["endTime"], row["JSSJ"])})
		}
	}
	body, err = timetablePost(ctx, requester, "kbbpapp/api/schoolCalendar/getTermWeeks.do", url.Values{"XNXQDM": {query.Term}})
	if err != nil {
		return nil, err
	}
	rows, err = timetableRows(body, "")
	if err != nil {
		return nil, err
	}
	weeks := []map[string]any{}
	for index, row := range rows {
		number := int(Number(firstText(row["serialNumber"], row["ZC"], row["DM"])))
		if number == 0 {
			number = index + 1
		}
		start, end := shortDate(firstText(row["startTime"], row["startDate"], row["beginDate"], row["KSRQ"])), shortDate(firstText(row["endTime"], row["endDate"], row["finishDate"], row["JSRQ"]))
		startDate, startErr := time.Parse("2006-01-02", start)
		endDate, endErr := time.Parse("2006-01-02", end)
		if startErr == nil && endErr == nil && startDate.Weekday() == time.Monday && endDate.Sub(startDate) == 6*24*time.Hour {
			start = startDate.AddDate(0, 0, -1).Format("2006-01-02")
			end = endDate.AddDate(0, 0, -1).Format("2006-01-02")
		}
		flag := row["curWeek"]
		if flag == nil {
			flag = row["current"]
		}
		current := flag == true || Text(flag) == "1" || Text(flag) == "true"
		weeks = append(weeks, map[string]any{"number": number, "name": firstText(row["name"], row["MC"], "第"+strconv.Itoa(number)+"周"), "start_date": start, "end_date": end, "current": current})
	}
	return map[string]any{"campuses": campuses, "weeks": weeks, "sections": sections}, nil
}
