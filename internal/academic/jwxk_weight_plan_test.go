package academic

import (
	"context"
	"testing"
)

func TestJWXKWeightPlanUsesOfficialMarketAndReclaimableBudget(t *testing.T) {
	input := JWXKWeightPlan{JWXKSavedPlan: JWXKSavedPlan{Batch: "round", Term: "2026-2027-1", Groups: []JWXKPlanGroup{{"g", "目标", 1}}, Items: []map[string]any{{"class_id": "class", "course_code": "CODE", "plan_group_id": "g", "utility": 9, "capacity": 9999, "weight_participant_count": 0}}}, GradeSize: 5000}
	market := []map[string]any{{"class_id": "class", "course_code": "CODE", "course_name": "课程", "source_scopes": []string{"FANKC"}, "capacity": 10, "weight_participant_count": 20}}
	official := map[string]any{"complete": true, "selection_type_code": "04", "term_code": input.Term, "selected": []map[string]any{}, "volunteered": []map[string]any{{"class_id": "old-class", "course_code": "CODE", "current_batch_record": true, "devoted_weight": 15, "selection_source": "fakcyx"}, {"class_id": "external", "course_code": "EXTERNAL", "current_batch_record": true, "devoted_weight": 50}}}
	budget := map[string]any{"remaining": 20, "total": 85, "used": 65, "minimum": 5, "step": 1}
	result, err := BuildJWXKWeightPlan(context.Background(), input, market, official, budget)
	if err != nil {
		t.Fatal(err)
	}
	item := result["items"].([]map[string]any)[0]
	if result["budget"] != 35 || result["reclaimable_weight"] != 15 || item["current_capacity"] != 10 || item["current_participant_count"] != 20 || item["weight"] != 35 || item["current_class_id"] != "old-class" || item["reapply_required"] != true {
		t.Fatal(result)
	}
	if _, exists := market[0]["weight"]; exists {
		t.Fatal("mutated source market")
	}
	market[0]["weight_participant_count"] = nil
	if _, err := BuildJWXKWeightPlan(context.Background(), input, market, official, budget); err == nil {
		t.Fatal("missing official bidders replaced with client values")
	}
	market[0]["weight_participant_count"] = 20
	official["complete"] = false
	if _, err := BuildJWXKWeightPlan(context.Background(), input, market, official, budget); err == nil {
		t.Fatal("incomplete results accepted")
	}
}

func TestJWXKWeightPlanIncludesWithdrawalForZeroBidExistingTarget(t *testing.T) {
	input := JWXKWeightPlan{JWXKSavedPlan: JWXKSavedPlan{Batch: "round", Term: "2026-2027-1", Groups: []JWXKPlanGroup{{"g", "目标", 1}}, Items: []map[string]any{{"class_id": "A", "course_code": "A", "plan_group_id": "g", "utility": 9}, {"class_id": "B", "course_code": "B", "plan_group_id": "g", "utility": 1}}}, GradeSize: 5000}
	market := []map[string]any{{"class_id": "A", "course_code": "A", "source_scopes": []string{"FANKC"}, "capacity": 10, "weight_participant_count": 0}, {"class_id": "B", "course_code": "B", "source_scopes": []string{"FANKC"}, "capacity": 10, "weight_participant_count": 0}}
	official := map[string]any{"complete": true, "selection_type_code": "04", "term_code": input.Term, "selected": []map[string]any{}, "volunteered": []map[string]any{{"class_id": "B", "course_code": "B", "current_batch_record": true, "devoted_weight": 5, "selection_source": "fakcyx"}}}
	result, err := BuildJWXKWeightPlan(context.Background(), input, market, official, map[string]any{"remaining": 0, "minimum": 5, "step": 1})
	if err != nil {
		t.Fatal(err)
	}
	items := result["items"].([]map[string]any)
	if len(items) != 2 {
		t.Fatal("model reclaimed weight without scheduling withdrawal", result)
	}
	for _, item := range items {
		if item["course_code"] == "B" && (item["withdraw_only"] != true || Number(item["weight"]) != 0) {
			t.Fatal(item)
		}
	}
}
