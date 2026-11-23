package main

import (
	"fmt"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func main() {
	exams := []map[string]any{{
		"task_id":               "calendar-acceptance",
		"course_name":           "日历验收课程",
		"exam_type":             "期末",
		"exam_place":            "教学楼,一层;东侧",
		"exam_time_description": "2026年05月08日 10:10-12:10(星期五第1场)",
	}}
	calendar, count := academic.ExamsCalendar(exams, "fixture", "2026-1", time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC))
	if count != 1 {
		panic("expected one calendar event")
	}
	fmt.Print(calendar)
}
