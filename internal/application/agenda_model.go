package application

import (
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

type AgendaEvent struct {
	ID        string   `json:"id"`
	Mode      string   `json:"selection_mode"`
	Date      *string  `json:"date"`
	Dates     []string `json:"dates"`
	Weekday   *int     `json:"weekday"`
	Weeks     []int    `json:"weeks"`
	Title     string   `json:"title"`
	Start     string   `json:"start_time"`
	End       string   `json:"end_time"`
	Location  string   `json:"location"`
	Note      string   `json:"note"`
	Important string   `json:"important"`
}
type AgendaMove struct {
	Source string `json:"source"`
	Target string `json:"target"`
}
type AgendaDocument struct {
	Revision int64         `json:"revision"`
	Events   []AgendaEvent `json:"events"`
	Moves    []AgendaMove  `json:"moves"`
}
type semesterSpan struct {
	Start string `json:"start"`
	End   string `json:"end"`
}
type agendaRecord struct {
	Events   []AgendaEvent `json:"events"`
	Moves    []AgendaMove  `json:"moves"`
	Semester *semesterSpan `json:"_semester,omitempty"`
	Retired  bool          `json:"_retired,omitempty"`
}

var agendaID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,80}$`)
var agendaClock = regexp.MustCompile(`^(?:[01]\d|2[0-3]):[0-5]\d$`)

func validDate(value string) bool {
	date, err := time.Parse("2006-01-02", value)
	return err == nil && date.Format("2006-01-02") == value
}
func agendaToday() string {
	return time.Now().In(time.FixedZone("Asia/Shanghai", 8*3600)).Format("2006-01-02")
}

func (document *AgendaDocument) normalize() bool {
	if document.Revision < 0 || len(document.Events) > 1000 || len(document.Moves) > 100 {
		return false
	}
	if document.Events == nil {
		document.Events = []AgendaEvent{}
	}
	if document.Moves == nil {
		document.Moves = []AgendaMove{}
	}
	ids, targets := map[string]bool{}, map[string]bool{}
	for index := range document.Events {
		event := &document.Events[index]
		if !agendaID.MatchString(event.ID) || ids[event.ID] || strings.TrimSpace(event.Title) == "" || len([]rune(event.Title)) > 120 || !agendaClock.MatchString(event.Start) || !agendaClock.MatchString(event.End) || event.Start >= event.End || len([]rune(event.Location)) > 200 || len([]rune(event.Note)) > 1000 || len([]rune(event.Important)) > 200 || len(event.Dates) > 100 || len(event.Weeks) > 30 {
			return false
		}
		ids[event.ID] = true
		if event.Mode == "" {
			event.Mode = "date"
		}
		switch event.Mode {
		case "date":
			dates := event.Dates
			if len(dates) == 0 && event.Date != nil {
				dates = []string{*event.Date}
			}
			if len(dates) == 0 {
				return false
			}
			seen := map[string]bool{}
			event.Dates = []string{}
			for _, date := range dates {
				if !validDate(date) {
					return false
				}
				if !seen[date] {
					event.Dates = append(event.Dates, date)
					seen[date] = true
				}
			}
			sort.Strings(event.Dates)
			event.Date = &event.Dates[0]
			event.Weekday = nil
			event.Weeks = []int{}
		case "week":
			if event.Weekday == nil || *event.Weekday < 1 || *event.Weekday > 7 || len(event.Weeks) == 0 {
				return false
			}
			seen := map[int]bool{}
			weeks := []int{}
			for _, week := range event.Weeks {
				if week < 1 || week > 30 {
					return false
				}
				if !seen[week] {
					weeks = append(weeks, week)
					seen[week] = true
				}
			}
			sort.Ints(weeks)
			event.Weeks = weeks
			event.Date = nil
			event.Dates = []string{}
		default:
			return false
		}
	}
	for _, move := range document.Moves {
		if !validDate(move.Source) || !validDate(move.Target) || move.Source == move.Target || targets[move.Target] {
			return false
		}
		targets[move.Target] = true
	}
	return true
}

func semesterCalendar(rows []map[string]any) *semesterSpan {
	if len(rows) == 0 || len(rows) > 30 {
		return nil
	}
	ordered := append([]map[string]any{}, rows...)
	sort.Slice(ordered, func(left, right int) bool {
		return academic.Number(ordered[left]["number"]) < academic.Number(ordered[right]["number"])
	})
	var previous time.Time
	for index, row := range ordered {
		startText, endText := academic.Text(row["start_date"]), academic.Text(row["end_date"])
		if !validDate(startText) || !validDate(endText) || academic.Number(row["number"]) != float64(index+1) {
			return nil
		}
		start, _ := time.Parse("2006-01-02", startText)
		end, _ := time.Parse("2006-01-02", endText)
		if start.Weekday() != time.Sunday || end.Sub(start) != 6*24*time.Hour || index > 0 && start.Sub(previous) != 24*time.Hour {
			return nil
		}
		previous = end
	}
	return &semesterSpan{Start: academic.Text(ordered[0]["start_date"]), End: academic.Text(ordered[len(ordered)-1]["end_date"])}
}
