package academic

import (
	"context"
	"encoding/json"
	"math"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

var targetFilters = map[string]map[string][2]string{
	"class":   {"grade": {"BJMC", "include"}, "college": {"YXDM", "equal"}, "major": {"ZYDM", "equal"}, "direction": {"ZYFXDM", "equal"}, "campus": {"XXXQDM", "equal"}, "has_schedule": {"SFPK", "equal"}},
	"teacher": {"department": {"SZDWDM", "equal"}, "title": {"ZCDM", "equal"}, "gender": {"XBDM", "equal"}, "external": {"SFWP", "equal"}, "has_schedule": {"SFPK", "equal"}},
	"room":    {"campus": {"XXXQDM", "equal"}, "building": {"JXLDM", "equal"}, "floor": {"LC", "equal"}, "room_type": {"JASLXDM", "equal"}, "department": {"GLDWDM", "equal"}, "use_scope": {"SYFWDM", "equal"}, "lab_center": {"SSSYZXDM", "equal"}, "min_capacity": {"RL", "moreEqual"}, "max_capacity": {"RL", "lessEqual"}, "has_schedule": {"SFPK", "equal"}},
}

type TimetableTargetQuery struct {
	Mode     string         `json:"mode"`
	Term     string         `json:"term_code"`
	Keyword  string         `json:"keyword"`
	Page     int            `json:"page"`
	PageSize int            `json:"page_size"`
	Filters  map[string]any `json:"filters"`
}

func safeFilterText(value string, max int) bool {
	if len([]rune(value)) > max {
		return false
	}
	for _, char := range value {
		if unicode.IsControl(char) {
			return false
		}
	}
	return true
}

func (query TimetableTargetQuery) Valid() bool {
	allowed, ok := targetFilters[query.Mode]
	if !ok || !timetableTermPattern.MatchString(query.Term) || query.Page < 1 || query.Page > 10000 || query.PageSize < 1 || query.PageSize > 50 || !safeFilterText(query.Keyword, 100) {
		return false
	}
	for key, value := range query.Filters {
		if _, ok := allowed[key]; !ok {
			return false
		}
		if value == nil {
			continue
		}
		if key == "min_capacity" || key == "max_capacity" {
			number, err := strconv.ParseFloat(Text(value), 64)
			if err != nil || math.IsNaN(number) || math.IsInf(number, 0) || number < 0 || number > 10000 || math.Trunc(number) != number {
				return false
			}
		} else {
			if _, ok := value.(string); !ok {
				return false
			}
			if !safeFilterText(Text(value), 64) {
				return false
			}
			if key == "has_schedule" && value != "yes" && value != "no" {
				return false
			}
		}
	}
	if query.Filters["min_capacity"] != nil && query.Filters["max_capacity"] != nil && Number(query.Filters["min_capacity"]) > Number(query.Filters["max_capacity"]) {
		return false
	}
	return true
}

var classGradePattern = regexp.MustCompile(`(?:^|[^0-9])([0-9]{2})[0-9]{2}(?:$|[^0-9])`)
var targetCodePattern = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func timetableTarget(mode string, row map[string]any) map[string]any {
	identifier := firstText(row["CODE"])
	if identifier == "" && mode == "teacher" {
		identifier = firstText(row["JGH"], row["JSBH"])
	}
	identifier = firstText(identifier, row["DM"], row["WID"])
	name := ""
	details, values := map[string]string{}, map[string]string{}
	var specs [][4]string
	switch mode {
	case "room":
		name = firstText(row["JASMC"], row["MC"], identifier)
		specs = [][4]string{{"campus", "XXXQDM", "XXXQMC", "campus"}, {"building", "JXLDM", "JXLMC", "building"}, {"type", "JASLXDM", "JASLXMC", "room_type"}, {"department", "GLDWDM", "GLDWMC", "department"}, {"use_scope", "SYFWDM", "SYFW", "use_scope"}, {"lab_center", "SSSYZXDM", "SSSYZX", "lab_center"}}
		floor := Text(row["LC"])
		if floor != "" {
			details["floor"] = floor
			if !strings.Contains(floor, "层") {
				details["floor"] += "层"
			}
		}
		values["floor"] = firstText(row["LCDM"], row["LC"])
		details["capacity"] = Text(row["RL"])
	case "teacher":
		name = firstText(row["JSMC"], row["XM"], row["MC"], identifier)
		specs = [][4]string{{"department", "SZDWDM", "SZDWMC", "department"}, {"title", "ZCDM", "ZCMC", "title"}, {"gender", "XBDM", "XB", "gender"}, {"external", "SFWP", "SFWP", "external"}}
	default:
		name = firstText(row["BJMC"], row["MC"], identifier)
		specs = [][4]string{{"college", "YXDM", "YXMC", "college"}, {"major", "ZYDM", "ZYMC", "major"}, {"direction", "ZYFXDM", "ZYFXMC", "direction"}, {"campus", "XXXQDM", "XXXQMC", "campus"}}
		grade := ""
		if match := classGradePattern.FindStringSubmatch(name); len(match) > 1 {
			grade = match[1]
		}
		details["grade"] = firstText(row["NJDM_DISPLAY"], row["NJMC"])
		if details["grade"] == "" && grade != "" {
			details["grade"] = "20" + grade + "级"
		}
		values["grade"] = firstText(row["NJDM"], grade)
	}
	for _, spec := range specs {
		details[spec[0]] = firstText(row[spec[1]+"_DISPLAY"], row[spec[2]])
		values[spec[3]] = Text(row[spec[1]])
	}
	for key, value := range details {
		if value == "" {
			delete(details, key)
		}
	}
	for key, value := range values {
		if value == "" {
			delete(values, key)
		}
	}
	return map[string]any{"id": identifier, "name": name, "has_schedule": firstText(row["SFPK_DISPLAY"], row["SFPK"]), "details": details, "filter_values": values}
}

func SearchTimetableTargets(ctx context.Context, requester Requester, query TimetableTargetQuery) (map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "查询对象条件无效"}
	}
	return searchTimetableTargets(ctx, requester, query)
}

func searchTimetableTargets(ctx context.Context, requester Requester, query TimetableTargetQuery) (map[string]any, error) {
	keyword := strings.TrimSpace(query.Keyword)
	fields := map[string][]string{"class": {"BJMC"}, "teacher": {"XM", "JSMC"}, "room": {"JASMC"}}[query.Mode]
	if keyword == "" {
		fields = []string{""}
	} else if targetCodePattern.MatchString(keyword) {
		if query.Mode == "room" {
			fields = append(fields, "CODE")
		} else {
			fields = append([]string{"CODE"}, fields...)
		}
	}
	action := map[string]string{"class": "bjlb", "teacher": "lslb", "room": "jslb"}[query.Mode]
	filterRules := []map[string]string{}
	keys := []string{}
	for key := range query.Filters {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		value := Text(query.Filters[key])
		if value == "" {
			continue
		}
		spec := targetFilters[query.Mode][key]
		if value == "yes" {
			value = "1"
		} else if value == "no" {
			value = "0"
		}
		filterRules = append(filterRules, map[string]string{"name": spec[0], "builder": spec[1], "linkOpt": "AND", "value": value})
	}
	targets := []map[string]any{}
	total := 0
	returnedPage, returnedSize := query.Page, query.PageSize
	for _, field := range fields {
		rules := append([]map[string]string{}, filterRules...)
		if field != "" {
			rules = append([]map[string]string{{"name": field, "builder": "include", "linkOpt": "AND", "value": keyword}}, rules...)
		}
		encoded, _ := json.Marshal(rules)
		body, err := timetablePost(ctx, requester, "kbapp/modules/qxkbcx/"+action+".do", url.Values{"XNXQDM": {query.Term}, "querySetting": {string(encoded)}, "pageNumber": {strconv.Itoa(query.Page)}, "pageSize": {strconv.Itoa(query.PageSize)}})
		if err != nil {
			return nil, err
		}
		rows, err := timetableRows(body, action)
		if err != nil {
			return nil, err
		}
		targets = []map[string]any{}
		validCount := 0
		for _, row := range rows {
			target := timetableTarget(query.Mode, row)
			if target["id"] == "" {
				continue
			}
			validCount++
			matches := keyword == ""
			for _, value := range []any{row["CODE"], row["JGH"], row["JSBH"], row["XM"], row["JSMC"], row["BJMC"], row["JASMC"], row["MC"], target["id"], target["name"]} {
				if strings.Contains(strings.ToLower(Text(value)), strings.ToLower(keyword)) {
					matches = true
				}
			}
			if matches {
				targets = append(targets, target)
			}
		}
		total = len(targets)
		container := Object(Object(Object(body)["datas"])[action])
		if value, exists := container["pageNumber"]; exists {
			returnedPage = int(Number(value))
		}
		if value, exists := container["pageSize"]; exists {
			returnedSize = int(Number(value))
		}
		if returnedPage != query.Page || returnedSize < 1 {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表查询对象分页未按请求推进"}
		}
		if value, ok := container["totalSize"]; ok {
			parsed, err := strconv.Atoi(Text(value))
			if err != nil || parsed < 0 {
				return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "查询对象总数无效"}
			}
			total = parsed
		}
		if keyword != "" && (len(targets) == 0 || len(targets) != validCount) {
			total = len(targets)
		}
		if keyword == "" || len(targets) > 0 {
			break
		}
	}
	return map[string]any{"items": targets, "total": total, "page": returnedPage, "page_size": returnedSize}, nil
}
