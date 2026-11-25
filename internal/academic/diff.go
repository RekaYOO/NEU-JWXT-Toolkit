package academic

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"reflect"
	"sort"
)

func ScoresDiff(previous, current json.RawMessage) map[string]any {
	indexed := func(payload json.RawMessage) (map[string]Score, *float64, *int) {
		var scores Scores
		json.Unmarshal(payload, &scores)
		result := map[string]Score{}
		for _, score := range scores.Semantic().Scores {
			key := score.Code + "\x1f" + score.Term
			if score.Code == "" {
				encoded, _ := json.Marshal(map[string]any{"name": score.Name, "term": score.Term, "credit": score.Credit, "course_type": score.CourseType})
				digest := sha256.Sum256(encoded)
				key = hex.EncodeToString(digest[:])
			}
			result[key] = score
		}
		return result, scores.OverallGPA, scores.OfficialMajorRank
	}
	before, oldGPA, oldRank := indexed(previous)
	after, newGPA, newRank := indexed(current)
	keys, seen := []string{}, map[string]bool{}
	for key := range before {
		keys = append(keys, key)
		seen[key] = true
	}
	for key := range after {
		if !seen[key] {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	added, removed, changed := []Score{}, []Score{}, []map[string]Score{}
	for _, key := range keys {
		old, had := before[key]
		newScore, has := after[key]
		if !had {
			added = append(added, newScore)
		} else if !has {
			removed = append(removed, old)
		} else if old != newScore {
			changed = append(changed, map[string]Score{"before": old, "after": newScore})
		}
	}
	return map[string]any{"added": added, "removed": removed, "changed": changed, "overall_gpa_changed": !reflect.DeepEqual(oldGPA, newGPA), "official_major_rank_changed": !reflect.DeepEqual(oldRank, newRank), "counts": map[string]int{"added": len(added), "removed": len(removed), "changed": len(changed)}}
}

func ReportDiff(previous, current json.RawMessage) map[string]any {
	if len(previous) == 0 {
		return map[string]any{"initial": true}
	}
	normalized := func(payload json.RawMessage) map[string]any {
		var report map[string]any
		json.Unmarshal(payload, &report)
		semantic, err := ReportSemantic(report)
		if err != nil {
			return report
		}
		json.Unmarshal(semantic, &report)
		return report
	}
	before, after := normalized(previous), normalized(current)
	return map[string]any{"credit_summary_changed": !reflect.DeepEqual(before["credit_summary"], after["credit_summary"]), "category_tree_changed": !reflect.DeepEqual(before["categories"], after["categories"]), "outside_courses_changed": !reflect.DeepEqual(before["outside_courses"], after["outside_courses"])}
}
