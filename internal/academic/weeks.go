package academic

import (
	"encoding/json"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var weekRange = regexp.MustCompile(`(\d{1,2})\s*[-~～—至]\s*(\d{1,2})`)
var weekNumber = regexp.MustCompile(`\d+`)
var weekExcluded = regexp.MustCompile(`(?:除|不含)\s*第?\s*(\d{1,2})\s*周?`)
var weekClause = regexp.MustCompile(`(?:第\s*)?\d{1,2}(?:\s*[-~～—至,，、]\s*\d{1,2})*\s*周(?:\s*[（(]?\s*[单双]\s*(?:周)?\s*[）)]?)?`)
var oddWeek = regexp.MustCompile(`单周|[（(]\s*单\s*[）)]`)
var evenWeek = regexp.MustCompile(`双周|[（(]\s*双\s*[）)]`)

func ParseWeeks(value any, strict bool) []int {
	result := map[int]bool{}
	add := func(raw string) {
		number, err := strconv.Atoi(raw)
		if err == nil && number >= 1 && number <= 30 {
			result[number] = true
		}
	}
	switch typed := value.(type) {
	case map[string]any:
		for _, key := range []string{"weeks", "weekList", "week_list", "week", "weekName", "weekText", "text", "name"} {
			for _, week := range ParseWeeks(typed[key], strict) {
				result[week] = true
			}
		}
	case []any:
		for _, item := range typed {
			for _, week := range ParseWeeks(item, strict) {
				result[week] = true
			}
		}
	case json.Number, float64, int:
		add(Text(typed))
	case string:
		text := strings.TrimSpace(typed)
		if strict {
			for _, clause := range weekClause.FindAllString(text, -1) {
				for _, week := range ParseWeeks(clause, false) {
					result[week] = true
				}
			}
		} else {
			if strings.Contains(text, "/") {
				parts := []string{}
				for _, part := range strings.Split(text, "/") {
					if strings.Contains(part, "周") {
						parts = append(parts, part)
					}
				}
				if len(parts) > 0 {
					text = strings.Join(parts, ",")
				}
			}
			for _, match := range weekRange.FindAllStringSubmatch(text, -1) {
				start, _ := strconv.Atoi(match[1])
				end, _ := strconv.Atoi(match[2])
				if start >= 1 && end <= 30 && start <= end {
					for number := start; number <= end; number++ {
						result[number] = true
					}
				}
			}
			for _, raw := range weekNumber.FindAllString(weekRange.ReplaceAllString(text, " "), -1) {
				add(raw)
			}
			odd, even := oddWeek.MatchString(text), evenWeek.MatchString(text)
			for number := range result {
				if odd && !even && number%2 == 0 || even && !odd && number%2 == 1 {
					delete(result, number)
				}
			}
		}
		for _, match := range weekExcluded.FindAllStringSubmatch(text, -1) {
			number, _ := strconv.Atoi(match[1])
			delete(result, number)
		}
	}
	weeks := []int{}
	for number := range result {
		weeks = append(weeks, number)
	}
	sort.Ints(weeks)
	return weeks
}
