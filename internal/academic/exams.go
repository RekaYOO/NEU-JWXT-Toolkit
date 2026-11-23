package academic

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

const homeBase = "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/"

func homeRows(ctx context.Context, requester Requester, method, path string, form url.Values) ([]map[string]any, error) {
	encoded, err := requester.Request(ctx, method, homeBase+path, form)
	if err != nil {
		return nil, err
	}
	payload, err := Decode(encoded)
	if err != nil {
		return nil, err
	}
	if Text(payload["code"]) != "0" {
		return nil, &campus.Error{Code: "ACADEMIC_RESPONSE_REJECTED", Message: "学校未确认查询成功"}
	}
	if payload["datas"] == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校响应缺少数据列表"}
	}
	return reportRows(payload["datas"])
}

func FetchExamTerms(ctx context.Context, requester Requester) (map[string]any, error) {
	rows, err := homeRows(ctx, requester, http.MethodGet, "kb/xnxq.do", nil)
	if err != nil {
		return nil, err
	}
	terms := []map[string]any{}
	current := ""
	for _, row := range rows {
		code := Text(row["itemCode"])
		if code == "" {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学期记录缺少编号"}
		}
		selected := row["selected"] == true
		terms = append(terms, map[string]any{"item_code": code, "item_name": Text(row["itemName"]), "selected": selected})
		if selected && current == "" {
			current = code
		}
	}
	if current == "" && len(terms) > 0 {
		current = Text(terms[0]["item_code"])
	}
	return map[string]any{"terms": terms, "current": current}, nil
}

func FetchExams(ctx context.Context, requester Requester, term string) (map[string]any, error) {
	terms, err := FetchExamTerms(ctx, requester)
	if err != nil {
		return nil, err
	}
	if term == "" {
		term = Text(terms["current"])
	}
	name := term
	for _, item := range terms["terms"].([]map[string]any) {
		if item["item_code"] == term {
			name = Text(item["item_name"])
			break
		}
	}
	exams := []map[string]any{}
	counts := map[int]int{}
	if term != "" {
		rows, err := homeRows(ctx, requester, http.MethodPost, "student/exams.do", url.Values{"termCode": {term}})
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			exam := map[string]any{}
			for _, field := range [][2]string{{"task_id", "taskId"}, {"course_name", "courseName"}, {"course_no", "courseNo"}, {"course_desc", "courseDesc"}, {"exam_type", "examType"}, {"exam_type_code", "examTypeCode"}, {"exam_date", "examDate"}, {"exam_time_description", "examTimeDescription"}, {"exam_place", "examPlace"}, {"exam_seat_no", "examSeatNo"}, {"teachers", "teachers"}, {"teaching_class_id", "teachingClassId"}} {
				exam[field[0]] = Text(row[field[1]])
			}
			status := int(Number(row["examStatus"]))
			exam["exam_status"], exam["week"], exam["start_time"], exam["end_time"] = status, int(Number(row["week"])), row["startTime"], row["endTime"]
			counts[status]++
			exams = append(exams, exam)
		}
	}
	return map[string]any{"term_code": term, "term_name": name, "total": len(exams), "upcoming": counts[0], "ongoing": counts[1], "finished": counts[2], "exams": exams}, nil
}

var examDatePattern = regexp.MustCompile(`(\d{4})年(\d{1,2})月(\d{1,2})日`)
var examClockPattern = regexp.MustCompile(`(\d{1,2}:\d{2})\s*[-—–]\s*(\d{1,2}:\d{2})`)

func examTimes(description string) (time.Time, time.Time, bool) {
	date, clock := examDatePattern.FindStringSubmatch(description), examClockPattern.FindStringSubmatch(description)
	if len(date) != 4 || len(clock) != 3 {
		return time.Time{}, time.Time{}, false
	}
	day := date[1] + "-" + date[2] + "-" + date[3]
	zone := time.FixedZone("Asia/Shanghai", 8*60*60)
	start, err := time.ParseInLocation("2006-1-2 15:04", day+" "+clock[1], zone)
	if err != nil {
		return time.Time{}, time.Time{}, false
	}
	end, err := time.ParseInLocation("2006-1-2 15:04", day+" "+clock[2], zone)
	if err != nil || !end.After(start) {
		return time.Time{}, time.Time{}, false
	}
	return start, end, true
}

func calendarText(value string) string {
	return strings.NewReplacer("\\", "\\\\", "\r\n", "\\n", "\r", "\\n", "\n", "\\n", ";", "\\;", ",", "\\,").Replace(value)
}

func foldCalendarLine(line string) string {
	var folded strings.Builder
	limit := 75
	for len(line) > limit {
		cut := limit
		for cut > 0 && !utf8.RuneStart(line[cut]) {
			cut--
		}
		folded.WriteString(line[:cut])
		folded.WriteString("\r\n ")
		line = line[cut:]
		limit = 74
	}
	if folded.Len() == 0 {
		return line
	}
	folded.WriteString(line)
	return folded.String()
}

func ExamsCalendar(exams []map[string]any, account, term string, now time.Time) (string, int) {
	lines := []string{"BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NEU-JWXT-Toolkit//Exam Schedule//ZH", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:NEU 考试安排", "X-WR-TIMEZONE:Asia/Shanghai"}
	count := 0
	for _, exam := range exams {
		start, end, ok := examTimes(Text(exam["exam_time_description"]))
		if !ok {
			continue
		}
		count++
		identity := firstText(exam["task_id"], Text(exam["course_no"])+"|"+Text(exam["exam_time_description"]))
		digest := sha256.Sum256([]byte(account + "\x1f" + term + "\x1f" + identity))
		description := "课程: " + Text(exam["course_name"]) + "\n类型: " + Text(exam["exam_type"]) + "\n时间: " + Text(exam["exam_time_description"]) + "\n地点: " + Text(exam["exam_place"]) + "\n座位: " + Text(exam["exam_seat_no"]) + "\n教师: " + Text(exam["teachers"]) + "\n课程号: " + Text(exam["course_no"])
		lines = append(lines, "BEGIN:VEVENT", "UID:"+hex.EncodeToString(digest[:])+"@neu-toolbox", "DTSTAMP:"+now.UTC().Format("20060102T150405Z"), "DTSTART:"+start.UTC().Format("20060102T150405Z"), "DTEND:"+end.UTC().Format("20060102T150405Z"), "SUMMARY:"+calendarText("【"+Text(exam["exam_type"])+"】"+Text(exam["course_name"])), "LOCATION:"+calendarText(Text(exam["exam_place"])), "DESCRIPTION:"+calendarText(description), "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:考试提醒", "TRIGGER:-PT24H", "END:VALARM", "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:考试即将开始", "TRIGGER:-PT2H", "END:VALARM", "END:VEVENT")
	}
	lines = append(lines, "END:VCALENDAR")
	for index := range lines {
		lines[index] = foldCalendarLine(lines[index])
	}
	return strings.Join(lines, "\r\n") + "\r\n", count
}
