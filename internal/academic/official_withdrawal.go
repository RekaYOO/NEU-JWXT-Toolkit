package academic

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const officialWithdrawalBase = "https://jwxt.neu.edu.cn/jwapp/sys/wsxksz/"

var officialTermPattern = regexp.MustCompile(`^[0-9-]{1,32}$`)
var officialWIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type OfficialWithdrawalInput struct {
	Term      string `json:"term_code"`
	WID       string `json:"wid"`
	Confirmed bool   `json:"confirmed"`
}

func (input OfficialWithdrawalInput) Valid() bool {
	return officialTermPattern.MatchString(input.Term) && officialWIDPattern.MatchString(input.WID) && input.Confirmed
}

func officialWithdrawalRows(ctx context.Context, requester Requester, operation string, query url.Values) ([]map[string]any, map[string]any, error) {
	target := officialWithdrawalBase + "modules/tkgl/" + operation + ".do"
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	encoded, err := requester.Request(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, nil, err
	}
	payload, err := Decode(encoded)
	if err != nil || Text(payload["code"]) != "0" {
		return nil, nil, errors.New("官方退课管理响应无效")
	}
	page := Object(Object(payload["datas"])[operation])
	values, ok := page["rows"].([]any)
	if !ok {
		return nil, nil, errors.New("官方退课管理响应结构异常")
	}
	rows := []map[string]any{}
	for _, value := range values {
		row, ok := value.(map[string]any)
		if !ok {
			return nil, nil, errors.New("官方退课记录结构异常")
		}
		rows = append(rows, row)
	}
	return rows, page, nil
}

func OfficialWithdrawalTerm(ctx context.Context, requester Requester) (string, string, error) {
	rows, _, err := officialWithdrawalRows(ctx, requester, "cxdqxkxnxq", nil)
	if err != nil {
		return "", "", err
	}
	if len(rows) == 0 {
		return "", "", nil
	}
	term := Text(rows[0]["DM"])
	if term != "" && !officialTermPattern.MatchString(term) {
		return "", "", errors.New("官方退课学期无效")
	}
	return term, Text(rows[0]["MC"]), nil
}

func officialNumber(value any) any {
	text := Text(value)
	if text == "" {
		return nil
	}
	number, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
		return nil
	}
	return number
}

func FetchOfficialWithdrawals(ctx context.Context, requester Requester, term string, now time.Time) ([]map[string]any, map[string]any, error) {
	if !officialTermPattern.MatchString(term) {
		return nil, nil, errors.New("官方退课学期无效")
	}
	rows := []map[string]any{}
	seen := map[string]bool{}
	for pageNumber := 1; pageNumber <= 20; pageNumber++ {
		current, page, err := officialWithdrawalRows(ctx, requester, "cxxsktrw", url.Values{"XNXQDM": {term}, "pageNumber": {strconv.Itoa(pageNumber)}, "pageSize": {"100"}, "querySetting": {"[]"}, "*order": {"+KCXH,+KCH,+KCM"}})
		if err != nil {
			return nil, nil, err
		}
		for _, row := range current {
			wid := Text(row["WID"])
			if !officialWIDPattern.MatchString(wid) || seen[wid] {
				return nil, nil, errors.New("官方退课列表标识缺失或重复")
			}
			seen[wid] = true
			rows = append(rows, row)
		}
		total := len(rows)
		if raw := Text(page["totalSize"]); raw != "" {
			parsed, err := strconv.Atoi(raw)
			if err != nil || parsed < 0 || parsed > 2000 {
				return nil, nil, errors.New("官方退课列表总数异常")
			}
			total = parsed
		}
		if len(rows) >= total {
			break
		}
		if len(current) == 0 || pageNumber == 20 {
			return nil, nil, errors.New("官方退课列表未能完整读取")
		}
	}
	rules := map[string]any{}
	for _, phase := range []string{"1", "2"} {
		values, _, err := officialWithdrawalRows(ctx, requester, "cxxqkcbl", url.Values{"PX": {phase}, "XNXQDM": {term}})
		if err == nil && len(values) > 0 {
			rules[phase] = map[string]any{"ratio": officialNumber(values[0]["KCBL"]), "label": Text(values[0]["KCBLMC"])}
		}
	}
	courses := []map[string]any{}
	zone := time.FixedZone("Asia/Shanghai", 8*3600)
	for _, row := range rows {
		course := map[string]any{}
		fields := map[string]string{"wid": "WID", "course_serial": "KCXH", "course_code": "KCH", "course_name": "KCM", "teaching_class_id": "JXBID", "department": "KKDWDM_DISPLAY", "teacher": "SKJS", "schedule": "YPSJDD", "course_nature": "KCXZDM_DISPLAY", "course_category": "KCLBDM_DISPLAY", "class_start_at": "KSSJ", "withdrawal_start_at": "TKKSSJ", "source_code": "XKLY", "source_label": "XKLY_DISPLAY"}
		for target, source := range fields {
			course[target] = Text(row[source])
		}
		endText := firstText(row["DSXSKSSJ"], row["DWXSKSSJ"])
		course["withdrawal_end_at"] = endText
		start, startErr := time.ParseInLocation("2006-01-02 15:04:05", Text(row["KSSJ"]), zone)
		end, endErr := time.ParseInLocation("2006-01-02 15:04:05", endText, zone)
		open, openErr := time.ParseInLocation("2006-01-02 15:04:05", Text(row["TKKSSJ"]), zone)
		reason, phase := "", ""
		switch {
		case Text(row["XKLY"]) != "01":
			reason = "非自选课程不允许退课"
		case openErr == nil && now.Before(open):
			reason = "尚未到官方退课开始时间"
		case startErr != nil || endErr != nil || end.Before(start) || Text(row["TKKSSJ"]) != "" && openErr != nil:
			reason = "退课时间暂不可确认"
		case now.Before(start):
			phase = "1"
		case !now.After(end):
			phase = "2"
		default:
			reason = "已超过允许退课时间"
		}
		course["can_withdraw"], course["unavailable_reason"], course["penalty_phase"] = reason == "", reason, phase
		penalty := Object(rules[phase])
		course["penalty_label"] = Text(penalty["label"])
		course["penalty_weight"] = nil
		weight := officialNumber(row["QZZ"])
		course["weight"] = weight
		if weight != nil && penalty["ratio"] != nil && phase != "" {
			value := weight.(float64) * penalty["ratio"].(float64)
			if value >= 0 && value < 1e9 {
				course["penalty_weight"] = int(math.Floor(value + 0.5))
			}
		}
		course["hours"], course["credits"] = officialNumber(row["XS"]), officialNumber(row["XF"])
		course["schedules"] = officialWithdrawalSchedules(row)
		courses = append(courses, course)
	}
	return courses, rules, nil
}

var withdrawalWeek = regexp.MustCompile(`(\d{1,2})\s*(?:[-~～—至]\s*(\d{1,2}))?\s*周`)
var withdrawalDay = regexp.MustCompile(`(?:星期|周)\s*([一二三四五六日天])`)
var withdrawalSection = regexp.MustCompile(`第\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*节(?:\s*[-~～—至]\s*第\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*节)?`)
var withdrawalSplit = regexp.MustCompile(`[，,；;\n]+`)

func officialWithdrawalSchedules(row map[string]any) []map[string]any {
	result := []map[string]any{}
	number := func(text string) int {
		if value, err := strconv.Atoi(text); err == nil {
			return value
		}
		return map[string]int{"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}[text]
	}
	for index, fragment := range withdrawalSplit.Split(Text(row["YPSJDD"]), -1) {
		fragment = strings.TrimSpace(fragment)
		week, day, section := withdrawalWeek.FindStringSubmatch(fragment), withdrawalDay.FindStringSubmatch(fragment), withdrawalSection.FindStringSubmatch(fragment)
		if week == nil || day == nil || section == nil {
			continue
		}
		first, last := number(week[1]), number(firstText(week[2], week[1]))
		start, end := number(section[1]), number(firstText(section[2], section[1]))
		if first < 1 || last < first || last > 30 || start < 1 || end < start {
			continue
		}
		weekday := map[string]int{"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}[day[1]]
		weeks := []int{}
		for current := first; current <= last; current++ {
			weeks = append(weeks, current)
		}
		location := ""
		parts := strings.Split(fragment, "/")
		if len(parts) >= 4 {
			location = strings.TrimSpace(parts[len(parts)-1])
		}
		result = append(result, map[string]any{"meeting_id": fmt.Sprintf("official-withdrawal-%s-%d", Text(row["WID"]), index), "course_code": Text(row["KCH"]), "course_name": Text(row["KCM"]), "teaching_class_id": firstText(row["JXBID"], row["WID"]), "weeks": weeks, "weekday": weekday, "start_section": start, "end_section": end, "teacher": Text(row["SKJS"]), "location": location, "raw_text": fragment, "layer": "preview"})
	}
	return result
}

func SendOfficialWithdrawal(ctx context.Context, requester Requester, input OfficialWithdrawalInput) (string, string) {
	if !input.Valid() {
		return "not_sent", "退课参数无效"
	}
	query, _ := json.Marshal([]map[string]string{{"name": "WID", "value": input.WID, "linkOpt": "and", "builder": "m_value_equal"}})
	encoded, err := requester.Request(ctx, http.MethodPost, officialWithdrawalBase+"tkglController/toTk.do", url.Values{"querySetting": {string(query)}, "XNXQDM": {input.Term}})
	if err != nil {
		return "unknown", "官方退课结果尚未确认，请刷新核验；不会重复提交"
	}
	payload, err := Decode(encoded)
	if err != nil || payload["code"] == nil {
		return "unknown", "官方退课响应异常，请刷新核验"
	}
	if _, err := strconv.Atoi(Text(payload["code"])); err != nil {
		return "unknown", "官方退课响应异常，请刷新核验"
	}
	if Text(payload["code"]) != "0" {
		return "rejected", "学校未接受退课请求，请刷新后核对"
	}
	return "confirmed", "官方退课请求已提交"
}
