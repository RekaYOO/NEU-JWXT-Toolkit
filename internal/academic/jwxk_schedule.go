package academic

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
)

var jwxkWeekMask = regexp.MustCompile(`^[01]{1,60}$`)
var jwxkWeekday = regexp.MustCompile(`(?:星期|周)\s*([一二三四五六日天])`)
var jwxkSection = regexp.MustCompile(`第?\s*(\d{1,2})\s*(?:[-~～—至]\s*第?\s*(\d{1,2}))?\s*节`)
var jwxkChineseSection = regexp.MustCompile(`第\s*([一二三四五六七八九十]{1,3})\s*节(?:\s*[-~～—至]\s*第\s*([一二三四五六七八九十]{1,3})\s*节)?`)
var jwxkFragmentSeparator = regexp.MustCompile(`[，；;\n]+`)

func jwxkID(prefix string, parts ...string) string {
	digest := sha256.Sum256([]byte(strings.Join(parts, "|")))
	length := 24
	if prefix == "jwxk-mtg-" || prefix == "jwxk-course-" {
		length = 20
	}
	return prefix + hex.EncodeToString(digest[:])[:length]
}
func jwxkInteger(value any) any {
	number, err := strconv.Atoi(Text(value))
	if err != nil {
		return nil
	}
	return number
}
func jwxkDecimal(value any) any {
	if value == nil || Text(value) == "" {
		return nil
	}
	number, err := strconv.ParseFloat(Text(value), 64)
	if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
		return nil
	}
	return number
}
func jwxkCampus(value any) string {
	code := strings.TrimSpace(Text(value))
	switch code {
	case "南湖校区":
		return "00"
	case "浑南校区":
		return "01"
	}
	return code
}
func jwxkCampusName(code, display string) string {
	if display != "" && display != code {
		return display
	}
	if code == "00" {
		return "南湖校区"
	}
	if code == "01" {
		return "浑南校区"
	}
	if code != "" {
		return "其他校区"
	}
	return ""
}

func jwxkSections(text string) (int, int) {
	if match := jwxkSection.FindStringSubmatch(text); len(match) > 0 {
		start, _ := strconv.Atoi(match[1])
		end := start
		if match[2] != "" {
			end, _ = strconv.Atoi(match[2])
		}
		if start > 0 && end >= start && end <= 30 {
			return start, end
		}
		return 0, 0
	}
	if match := jwxkChineseSection.FindStringSubmatch(text); len(match) > 0 {
		parse := func(value string) int {
			digits := map[rune]int{'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
			total, current := 0, 0
			for _, character := range value {
				if character == '十' {
					if current == 0 {
						current = 1
					}
					total += current * 10
					current = 0
				} else {
					current = digits[character]
				}
			}
			return total + current
		}
		start, end := parse(match[1]), parse(match[2])
		if match[2] == "" {
			end = start
		}
		if start > 0 && end >= start && end <= 30 {
			return start, end
		}
	}
	return 0, 0
}
func jwxkDay(text string) int {
	match := jwxkWeekday.FindStringSubmatch(text)
	if len(match) == 0 {
		return 0
	}
	switch match[1] {
	case "一":
		return 1
	case "二":
		return 2
	case "三":
		return 3
	case "四":
		return 4
	case "五":
		return 5
	case "六":
		return 6
	default:
		return 7
	}
}
func nullableSection(value int) any {
	if value <= 0 {
		return nil
	}
	return value
}

func jwxkFragments(text string) []map[string]any {
	result := []map[string]any{}
	for _, raw := range jwxkFragmentSeparator.Split(text, -1) {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		parts := strings.Split(raw, "/")
		weekText, dayText, sectionText := parts[0], raw, raw
		if len(parts) > 1 {
			dayText = parts[1]
		}
		if len(parts) > 2 {
			sectionText = parts[2]
		}
		weekText = teacherBracket.ReplaceAllString(weekText, "")
		start, end := jwxkSections(sectionText)
		teacher, location := "", ""
		if len(parts) >= 5 {
			teacher = strings.TrimSpace(parts[len(parts)-2])
			location = strings.TrimSpace(parts[len(parts)-1])
		}
		result = append(result, map[string]any{"raw_text": raw, "week_text": strings.TrimSpace(weekText), "weeks": ParseWeeks(weekText, len(parts) < 2), "weekday": jwxkDay(dayText), "start_section": start, "end_section": end, "teacher": teacher, "location": location})
	}
	return result
}

func jwxkMatchFragment(fragments []map[string]any, weeks []int, day, start, end int, teacher string) map[string]any {
	bestScore := -1
	var best map[string]any
	tie := false
	for _, fragment := range fragments {
		score := 0
		conflict := false
		for _, field := range []struct {
			key           string
			value, weight int
		}{{"weekday", day, 8}, {"start_section", start, 4}, {"end_section", end, 4}} {
			actual := int(Number(fragment[field.key]))
			if field.value > 0 && actual == field.value {
				score += field.weight
			} else if field.value > 0 && actual > 0 {
				conflict = true
			}
		}
		if conflict {
			continue
		}
		if teacher != "" && Text(fragment["teacher"]) == teacher {
			score += 2
		}
		fragmentWeeks := fragment["weeks"].([]int)
		if len(weeks) > 0 && len(fragmentWeeks) > 0 {
			if reflect.DeepEqual(weeks, fragmentWeeks) {
				score += 3
			} else {
				overlap := false
				for _, week := range weeks {
					for _, other := range fragmentWeeks {
						if week == other {
							overlap = true
						}
					}
				}
				if !overlap {
					continue
				}
				score++
			}
		}
		if score > bestScore {
			best, bestScore, tie = fragment, score, false
		} else if score == bestScore {
			tie = true
		}
	}
	if tie {
		return nil
	}
	return best
}

func jwxkSchedules(row, course map[string]any, classID string) []map[string]any {
	result := []map[string]any{}
	fragments := jwxkFragments(firstText(course["YPSJDD"], course["teachingPlace"]))
	meetings, _ := reportRows(row["SKSJ"])
	for index, item := range meetings {
		text, mask := Text(item["SKZCMC"]), Text(item["SKZC"])
		maskWeeks := []int{}
		if jwxkWeekMask.MatchString(mask) {
			for position, enabled := range mask {
				if enabled == '1' {
					maskWeeks = append(maskWeeks, position+1)
				}
			}
		}
		textWeeks := ParseWeeks(text, false)
		weeks, status := maskWeeks, "parsed"
		if len(maskWeeks) > 0 {
			if len(textWeeks) > 0 && !reflect.DeepEqual(maskWeeks, textWeeks) {
				status = "mismatch"
			}
		} else if len(textWeeks) > 0 {
			weeks, status = textWeeks, "fallback_text"
		} else {
			weeks, status = []int{}, "unknown"
		}
		day, start, end := int(Number(item["SKXQ"])), int(Number(item["KSJC"])), int(Number(item["JSJC"]))
		teacher := Text(item["SKJS"])
		fragment := jwxkMatchFragment(fragments, weeks, day, start, end, teacher)
		location, raw := "", ""
		if fragment != nil {
			location, raw = Text(fragment["location"]), Text(fragment["raw_text"])
		}
		campus := jwxkCampus(item["XXXQDM"])
		result = append(result, map[string]any{"meeting_id": jwxkID("jwxk-mtg-", classID, fmt.Sprint(index), firstText(mask, text), fmt.Sprint(day), fmt.Sprint(start), fmt.Sprint(end), teacher), "term_code": Text(item["XNXQ"]), "week_text": text, "week_mask": mask, "weeks": weeks, "recurrence_unknown": len(weeks) == 0, "parse_status": status, "weekday": nullableSection(day), "start_section": nullableSection(start), "end_section": nullableSection(end), "teacher": teacher, "campus": campus, "campus_name": jwxkCampusName(campus, firstText(item["XXXQDM_DISPLAY"], item["XXXQMC"])), "location": location, "raw_text": raw})
	}
	if len(meetings) == 0 {
		for index, fragment := range fragments {
			weeks := fragment["weeks"].([]int)
			day, start, end := int(Number(fragment["weekday"])), int(Number(fragment["start_section"])), int(Number(fragment["end_section"]))
			if len(weeks) == 0 || day == 0 || start == 0 || end == 0 {
				continue
			}
			campus := jwxkCampus(firstText(course["XXXQDM"], course["campusCode"]))
			result = append(result, map[string]any{"meeting_id": jwxkID("jwxk-mtg-", classID, "official", fmt.Sprint(index), Text(fragment["raw_text"])), "term_code": firstText(course["XNXQ"], course["schoolTerm"], course["termCode"], course["semester"], course["xnxq"]), "week_text": fragment["week_text"], "week_mask": "", "weeks": weeks, "recurrence_unknown": false, "parse_status": "official_schedule", "schedule_source": "official_schedule", "weekday": day, "start_section": start, "end_section": end, "teacher": firstText(fragment["teacher"], course["SKJS"]), "campus": campus, "campus_name": jwxkCampusName(campus, Text(course["XXXQMC"])), "location": fragment["location"], "raw_text": fragment["raw_text"]})
		}
	}
	return result
}

func jwxkExperiments(course map[string]any, classID string) []map[string]any {
	result := []map[string]any{}
	seen := map[string]bool{}
	rows, _ := reportRows(course["SYPKSJ"])
	for index, row := range rows {
		raw := Text(row["sksj"])
		id := Text(row["pklcwid"])
		if id == "" {
			id = jwxkID("", classID, Text(row["xmmc"]), raw, Text(row["xsfzwid"]), fmt.Sprint(index))
		}
		if seen[id] {
			continue
		}
		seen[id] = true
		start, end := jwxkSections(raw)
		weeks := ParseWeeks(raw, true)
		day := jwxkDay(raw)
		status := "unknown"
		if len(weeks) > 0 && day > 0 && start > 0 {
			status = "parsed"
		}
		campus := jwxkCampus(row["xqdm"])
		weekText := raw
		if position := jwxkWeekday.FindStringIndex(raw); len(position) > 0 {
			weekText = strings.TrimSpace(raw[:position[0]])
		}
		result = append(result, map[string]any{"schedule_id": id, "meeting_id": "jwxk-exp-" + id, "project_name": firstText(row["xmmc"], "未命名实验"), "week_text": weekText, "weeks": weeks, "recurrence_unknown": len(weeks) == 0, "parse_status": status, "weekday": nullableSection(day), "start_section": nullableSection(start), "end_section": nullableSection(end), "location": firstText(row["skddmc"], row["skdd"]), "campus": campus, "campus_name": jwxkCampusName(campus, ""), "teacher": Text(row["skjsxm"]), "group_id": Text(row["xsfzwid"]), "group_name": Text(row["xsfz"]), "target_classes": Text(row["lc"]), "hours": jwxkDecimal(row["syxs"]), "raw_text": raw})
	}
	return result
}
