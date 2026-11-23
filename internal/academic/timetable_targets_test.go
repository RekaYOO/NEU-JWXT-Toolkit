package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"testing"
)

func TestTeacherSearchFallsBackAndVerifiesMatches(t *testing.T) {
	calls := 0
	requester := requestFunc(func(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
		calls++
		var rules []map[string]string
		if err := json.Unmarshal([]byte(form.Get("querySetting")), &rules); err != nil {
			t.Fatal(err)
		}
		if calls == 1 {
			if rules[0]["name"] != "XM" {
				t.Fatal(rules)
			}
			return []byte(`{"datas":{"lslb":{"totalSize":500,"rows":[{"CODE":"other","XM":"其他教师"}]}}}`), nil
		}
		if rules[0]["name"] != "JSMC" {
			t.Fatal(rules)
		}
		return []byte(`{"datas":{"lslb":{"totalSize":500,"rows":[{"JGH":"target","JSMC":"测试教师","SZDWDM":"D","SZDWDM_DISPLAY":"学院"},{"CODE":"other","XM":"其他教师"}]}}}`), nil
	})
	result, err := SearchTimetableTargets(context.Background(), requester, TimetableTargetQuery{Mode: "teacher", Term: "2026-1", Keyword: "测试", Page: 1, PageSize: 20})
	if err != nil || calls != 2 || result["total"] != 1 {
		t.Fatalf("result: %+v %v calls=%d", result, err, calls)
	}
	target := result["items"].([]map[string]any)[0]
	if target["id"] != "target" || target["details"].(map[string]string)["department"] != "学院" {
		t.Fatal(target)
	}
}

func TestRoomFiltersUseOfficialCapacityAndAvailabilityFields(t *testing.T) {
	requester := requestFunc(func(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
		var rules []map[string]string
		json.Unmarshal([]byte(form.Get("querySetting")), &rules)
		found := map[string]string{}
		for _, rule := range rules {
			found[rule["name"]+":"+rule["builder"]] = rule["value"]
		}
		if found["RL:moreEqual"] != "30" || found["SFPK:equal"] != "1" {
			t.Fatal(rules)
		}
		return []byte(`{"datas":{"jslb":{"totalSize":1,"rows":[{"CODE":"R","JASMC":"教室","LC":3,"RL":40,"XXXQDM":"C","XXXQDM_DISPLAY":"校区"}]}}}`), nil
	})
	result, err := SearchTimetableTargets(context.Background(), requester, TimetableTargetQuery{Mode: "room", Term: "2026-1", Page: 1, PageSize: 20, Filters: map[string]any{"min_capacity": 30.0, "has_schedule": "yes"}})
	if err != nil {
		t.Fatal(err)
	}
	target := result["items"].([]map[string]any)[0]
	if target["details"].(map[string]string)["floor"] != "3层" || target["filter_values"].(map[string]string)["campus"] != "C" {
		t.Fatal(target)
	}
}

func TestTargetQueryRejectsCrossModeAndInvalidRanges(t *testing.T) {
	for _, filters := range []map[string]any{{"title": "professor"}, {"min_capacity": 50.0, "max_capacity": 10.0}, {"min_capacity": 1.5}, {"campus": "bad\nvalue"}} {
		if (TimetableTargetQuery{Mode: "room", Term: "2026-1", Page: 1, PageSize: 20, Filters: filters}).Valid() {
			t.Fatalf("invalid filter accepted: %+v", filters)
		}
	}
}
