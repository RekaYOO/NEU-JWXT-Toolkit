package academic

import (
	"context"
	"errors"
	"math"
	"testing"
	"time"
)

func TestWeightOptimizerMatchesLegacyFixture(t *testing.T) {
	market := []WeightMarketCourse{{"A", 30, 35}, {"B", 30, 30}, {"C", 30, 19}, {"D", 80, 58}}
	candidates := []WeightCandidate{
		{ID: "A", Name: "A", Capacity: 30, Bidders: 35, Utility: 10, Groups: []string{"g1"}},
		{ID: "B", Name: "B", Capacity: 30, Bidders: 30, Utility: 8, Groups: []string{"g1"}},
		{ID: "C", Name: "C", Capacity: 30, Bidders: 19, Utility: 7, Groups: []string{"g1"}},
		{ID: "D", Name: "D", Capacity: 80, Bidders: 58, Utility: 9, Groups: []string{"g2"}},
	}
	result, err := OptimizeGroupedWeights(context.Background(), WeightPolicy{Budget: 105, Minimum: 5, Step: 1}, 126, market, candidates, []JWXKPlanGroup{{"g1", "A", 2}, {"g2", "B", 1}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result["budget_used"] != 105 || result["nodes_visited"] != 14 || result["approximate"] != false {
		t.Fatal(result)
	}
	expected := []int{73, 27, 0, 5}
	for index, course := range result["courses"].([]map[string]any) {
		if course["bid"] != expected[index] {
			t.Fatal(course)
		}
	}
	first := result["courses"].([]map[string]any)[0]
	if math.Abs(first["scenario_success_rates"].(map[string]float64)["neutral"]-0.9937976924957643) > 1e-12 {
		t.Fatal(first)
	}
}

func TestWeightOptimizerConstraintsAndForecasts(t *testing.T) {
	for _, scenario := range []string{"conflict", "fixed", "safe", "stale", "mismatch", "approximate", "steps"} {
		t.Run(scenario, func(t *testing.T) {
			policy := WeightPolicy{Budget: 20, Minimum: 5, Step: 1}
			market := []WeightMarketCourse{{"A", 30, 90}, {"B", 30, 30}}
			candidates := []WeightCandidate{{ID: "A", Capacity: 30, Bidders: 35, Utility: 10, Groups: []string{"g"}}, {ID: "B", Capacity: 30, Bidders: 30, Utility: 9, Groups: []string{"g"}}}
			grade := 100
			var conflicts [][2]string
			switch scenario {
			case "conflict":
				conflicts = [][2]string{{"A", "B"}}
			case "fixed":
				candidates[0].AlreadySelected = true
			case "safe":
				policy.Budget = 105
				candidates[0].Bidders = 20
			case "stale":
				candidates[0].Bidders = 20
				candidates[0].TimeUnknown = true
			case "mismatch":
				grade, policy.Budget = 10, 10
			case "approximate":
				policy.NodeLimit = 2
			case "steps":
				policy.Minimum, policy.Step = 6, 3
			}
			result, err := OptimizeGroupedWeights(context.Background(), policy, grade, market, candidates, []JWXKPlanGroup{{"g", "目标", 2}}, conflicts)
			if err != nil {
				t.Fatal(err)
			}
			courses := result["courses"].([]map[string]any)
			if result["budget_used"].(int) > policy.Budget {
				t.Fatal(result)
			}
			for _, course := range courses {
				bid := course["bid"].(int)
				if bid != 0 && (bid < policy.Minimum || bid%policy.Step != 0) {
					t.Fatal(course)
				}
			}
			switch scenario {
			case "conflict":
				if courses[1]["bid"] != 0 || result["groups"].([]map[string]any)[0]["missing_count"] != 1 {
					t.Fatal(result)
				}
			case "fixed":
				if courses[0]["classification"] != "SELECTED" || courses[0]["bid"] != 0 || courses[1]["bid"] != 20 {
					t.Fatal(result)
				}
			case "safe":
				if courses[0]["bid"] != 5 || courses[1]["bid"] != 100 {
					t.Fatal(result)
				}
			case "stale":
				if result["diagnostics"].(map[string]any)["total_current_bidders"] != float64(50) || courses[0]["classification"] != "SAFE" || len(result["warnings"].([]string)) == 0 {
					t.Fatal(result)
				}
			case "mismatch":
				if courses[0]["forecast_status"] != "scope_mismatch" {
					t.Fatal(result)
				}
			case "approximate":
				if result["approximate"] != true {
					t.Fatal(result)
				}
			}
		})
	}
}

func TestWeightOptimizerCancellationAndInvalidValues(t *testing.T) {
	policy := WeightPolicy{Budget: 20, Minimum: 5, Step: 1, TimeLimit: time.Second}
	market := []WeightMarketCourse{{"A", 30, 35}}
	candidates := []WeightCandidate{{ID: "A", Capacity: 30, Bidders: 35, Utility: 10, Groups: []string{"g"}}}
	groups := []JWXKPlanGroup{{"g", "目标", 1}}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := OptimizeGroupedWeights(ctx, policy, 100, market, candidates, groups, nil); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	candidates[0].Utility = math.NaN()
	if _, err := OptimizeGroupedWeights(context.Background(), policy, 100, market, candidates, groups, nil); err == nil {
		t.Fatal("nonfinite utility accepted")
	}
	candidates[0].Utility, candidates[0].Groups = 5, []string{"g", "g"}
	if _, err := OptimizeGroupedWeights(context.Background(), policy, 100, market, candidates, groups, nil); err == nil {
		t.Fatal("duplicate memberships inflated group count")
	}
}
