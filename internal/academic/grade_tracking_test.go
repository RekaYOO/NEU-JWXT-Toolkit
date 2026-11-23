package academic

import (
	"testing"
	"time"
)

func TestGradeTrackingScheduleUsesSchoolTimezoneAndInitialBypassesWindow(t *testing.T) {
	config := DefaultGradeTrackingConfig()
	now := time.Date(2026, 11, 16, 12, 45, 0, 0, time.UTC)
	next, err := config.NextCheck(now, false)
	if err != nil || next.Hour() != 9 || next.Day() != 17 {
		t.Fatal(next, err)
	}
	if first, err := config.NextCheck(now, true); err != nil || !first.Equal(now) {
		t.Fatal(first, err)
	}
	if !config.WithinWindow(now) || config.WithinWindow(now.Add(15*time.Minute)) {
		t.Fatal("school window boundary incorrect")
	}
	config.StartHour, config.EndHour = 0, 24
	if next, err := config.NextCheck(now, false); err != nil || !next.Equal(now.Add(30*time.Minute)) {
		t.Fatal(next, err)
	}
	config.IntervalMinutes = 4
	if config.Valid() {
		t.Fatal("sub-five-minute polling accepted")
	}
	config.IntervalMinutes = 30
	config.StartHour, config.EndHour = 21, 9
	if config.Valid() {
		t.Fatal("reversed window accepted")
	}
}

func TestGradeTrackingSnapshotIgnoresTransportReferencesAndSelectsOnlyNeededDetails(t *testing.T) {
	now := time.Now()
	first := Score{Code: "A", Name: "课程A", Term: "2026-2027-1", Score: "80", GPA: 3, Credit: 2, DetailRef: "private-reference"}
	second := Score{Code: "B", Name: "课程B", Term: first.Term, Score: "90", GPA: 4, Credit: 2}
	before, err := NewGradeTrackingSnapshot(Scores{Scores: []Score{first, second}}, now)
	if err != nil {
		t.Fatal(err)
	}
	first.DetailRef = "changed-reference"
	reordered, _ := NewGradeTrackingSnapshot(Scores{Scores: []Score{second, first}}, now.Add(time.Hour))
	if before.Revision != reordered.Revision || CompareGradeTracking(before, reordered).HasChanges() {
		t.Fatal("transport update changed semantic snapshot")
	}
	if before.Scores.Scores[0].DetailRef != "" {
		t.Fatal("private reference stored in tracking snapshot")
	}
	first.Name = "改名"
	after, _ := NewGradeTrackingSnapshot(Scores{Scores: []Score{first, second}}, now)
	change := CompareGradeTracking(before, after)
	if len(change.Changed) != 1 || len(change.DetailCourses) != 0 {
		t.Fatal("name-only change requested details", change)
	}
	first.Score, first.GPA = "85", 3.5
	third := Score{Code: "C", Name: "新增", Term: first.Term, Score: "70", GPA: 2, Credit: 1}
	after, _ = NewGradeTrackingSnapshot(Scores{Scores: []Score{first, third}}, now)
	change = CompareGradeTracking(before, after)
	if len(change.Added) != 1 || len(change.Changed) != 1 || len(change.Removed) != 1 || len(change.DetailCourses) != 2 {
		t.Fatal(change)
	}
	gpa := 3.1
	after, _ = NewGradeTrackingSnapshot(Scores{Scores: before.Scores.Scores, OverallGPA: &gpa}, now)
	change = CompareGradeTracking(before, after)
	if !change.HasChanges() || !change.OverallGPAChanged || len(change.DetailCourses) != 0 {
		t.Fatal("overall GPA triggered details", change)
	}
}
