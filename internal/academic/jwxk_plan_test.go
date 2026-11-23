package academic

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestJWXKPlanLegacyWeeksAndGroupSurviveRoundTrip(t *testing.T) {
	var input JWXKSavedPlan
	if err := json.Unmarshal([]byte(`{"batch_code":"round","term_code":"term","items":[{"course_code":"C1","priority":3,"schedules":[{"week_mask":"1010","weekday":1,"start_section":1,"end_section":2}]}]}`), &input); err != nil {
		t.Fatal(err)
	}
	plan := NormalizeJWXKPlan(input)
	if plan.Items[0]["plan_group_id"] != "C1" || plan.Items[0]["utility"] != 8 {
		t.Fatal(plan)
	}
	meeting := plan.Items[0]["schedules"].([]map[string]any)[0]
	if !reflect.DeepEqual(meeting["weeks"], []int{1, 3}) || meeting["recurrence_unknown"] != false {
		t.Fatal(meeting)
	}
	encoded, _ := json.Marshal(plan)
	json.Unmarshal(encoded, &input)
	again := NormalizeJWXKPlan(input)
	if again.Items[0]["schedules"].([]map[string]any)[0]["meeting_id"] != meeting["meeting_id"] {
		t.Fatal("meeting identity changed")
	}
}
