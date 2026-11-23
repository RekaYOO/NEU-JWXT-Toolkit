package academic

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"time"
)

type GradeTrackingConfig struct {
	Enabled         bool `json:"enabled"`
	IntervalMinutes int  `json:"interval_minutes"`
	StartHour       int  `json:"start_hour"`
	EndHour         int  `json:"end_hour"`
}

func DefaultGradeTrackingConfig() GradeTrackingConfig {
	return GradeTrackingConfig{IntervalMinutes: 30, StartHour: 9, EndHour: 21}
}

func (config GradeTrackingConfig) Valid() bool {
	return config.IntervalMinutes >= 5 && config.IntervalMinutes <= 1440 && config.StartHour >= 0 && config.StartHour <= 23 && config.EndHour >= 1 && config.EndHour <= 24 && config.StartHour < config.EndHour
}

func (config GradeTrackingConfig) NextCheck(now time.Time, initial bool) (time.Time, error) {
	if !config.Valid() {
		return time.Time{}, errors.New("invalid grade tracking schedule")
	}
	if initial {
		return now, nil
	}
	zone := time.FixedZone("Asia/Shanghai", 8*3600)
	next := now.In(zone).Add(time.Duration(config.IntervalMinutes) * time.Minute)
	if next.Hour() < config.StartHour {
		return time.Date(next.Year(), next.Month(), next.Day(), config.StartHour, 0, 0, 0, zone), nil
	}
	if next.Hour() >= config.EndHour {
		return time.Date(next.Year(), next.Month(), next.Day()+1, config.StartHour, 0, 0, 0, zone), nil
	}
	return next, nil
}

func (config GradeTrackingConfig) WithinWindow(now time.Time) bool {
	hour := now.In(time.FixedZone("Asia/Shanghai", 8*3600)).Hour()
	return config.Valid() && hour >= config.StartHour && hour < config.EndHour
}

type GradeTrackingSnapshot struct {
	Scores   Scores `json:"data"`
	Revision string `json:"revision"`
	Updated  string `json:"updated_at"`
}

func NewGradeTrackingSnapshot(scores Scores, now time.Time) (GradeTrackingSnapshot, error) {
	semantic := scores.Semantic()
	sort.SliceStable(semantic.Scores, func(left, right int) bool {
		first, second := semantic.Scores[left], semantic.Scores[right]
		if first.Code != second.Code {
			return first.Code < second.Code
		}
		if first.Term != second.Term {
			return first.Term < second.Term
		}
		encodedFirst, _ := json.Marshal(first)
		encodedSecond, _ := json.Marshal(second)
		return string(encodedFirst) < string(encodedSecond)
	})
	payload, err := json.Marshal(semantic)
	if err != nil {
		return GradeTrackingSnapshot{}, err
	}
	digest := sha256.Sum256(payload)
	return GradeTrackingSnapshot{Scores: semantic, Revision: "v1:" + hex.EncodeToString(digest[:]), Updated: now.UTC().Format(time.RFC3339Nano)}, nil
}

type GradeTrackingChange struct {
	Added             []Score            `json:"added"`
	Changed           []map[string]Score `json:"changed"`
	Removed           []Score            `json:"removed"`
	OverallGPAChanged bool               `json:"overall_gpa_changed"`
	DetailCourses     []Score            `json:"detail_courses"`
}

func CompareGradeTracking(previous, current GradeTrackingSnapshot) GradeTrackingChange {
	before, _ := json.Marshal(previous.Scores)
	after, _ := json.Marshal(current.Scores)
	diff := ScoresDiff(before, after)
	result := GradeTrackingChange{Added: diff["added"].([]Score), Changed: diff["changed"].([]map[string]Score), Removed: diff["removed"].([]Score), OverallGPAChanged: diff["overall_gpa_changed"].(bool), DetailCourses: []Score{}}
	seen := map[string]bool{}
	include := func(score Score) {
		key := score.Code + "\x1f" + score.Term
		if score.Code != "" && score.Term != "" && !seen[key] {
			result.DetailCourses = append(result.DetailCourses, score)
			seen[key] = true
		}
	}
	for _, score := range result.Added {
		include(score)
	}
	for _, change := range result.Changed {
		if change["before"].Score != change["after"].Score || change["before"].GPA != change["after"].GPA {
			include(change["after"])
		}
	}
	return result
}

func (change GradeTrackingChange) HasChanges() bool {
	return len(change.Added)+len(change.Changed)+len(change.Removed) > 0 || change.OverallGPAChanged
}
