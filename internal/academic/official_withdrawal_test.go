package academic

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestOfficialWithdrawalFieldsWindowsAndSchedule(t *testing.T) {
	requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		if method != "GET" {
			t.Fatal("read sent mutation")
		}
		parsed, _ := url.Parse(target)
		if strings.Contains(target, "cxxqkcbl") {
			return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"cxxqkcbl": map[string]any{"rows": []any{map[string]any{"KCBL": 0.5, "KCBLMC": "50%"}}}}})
		}
		if parsed.Query().Get("XNXQDM") != "2099-2100-1" || parsed.Query().Get("pageSize") != "100" {
			t.Fatal(parsed.Query())
		}
		rows := []any{}
		for _, source := range []string{"01", "02"} {
			rows = append(rows, map[string]any{"WID": "W" + source, "KCH": "C1", "KCM": "匿名课程", "XKLY": source, "KSSJ": "2099-09-01 08:00:00", "DSXSKSSJ": "2099-09-08 08:00:00", "QZZ": "5", "XF": 2, "YPSJDD": "1-16周/星期一/第一节-第二节/教室A"})
		}
		return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"cxxsktrw": map[string]any{"rows": rows, "totalSize": 2}}})
	})
	before := time.Date(2099, 8, 1, 0, 0, 0, 0, time.UTC)
	courses, rules, err := FetchOfficialWithdrawals(context.Background(), requester, "2099-2100-1", before)
	if err != nil || len(courses) != 2 || len(rules) != 2 {
		t.Fatal(courses, rules, err)
	}
	if courses[0]["can_withdraw"] != true || courses[0]["penalty_weight"] != 3 || courses[1]["can_withdraw"] != false {
		t.Fatal(courses)
	}
	schedules := courses[0]["schedules"].([]map[string]any)
	if len(schedules) != 1 || schedules[0]["weekday"] != 1 || schedules[0]["end_section"] != 2 || schedules[0]["location"] != "教室A" {
		t.Fatal(schedules)
	}
	courses, _, err = FetchOfficialWithdrawals(context.Background(), requester, "2099-2100-1", time.Date(2100, 1, 1, 0, 0, 0, 0, time.UTC))
	if err != nil || courses[0]["can_withdraw"] != false {
		t.Fatal(courses, err)
	}
}

func TestOfficialWithdrawalPaginationAndTimeGuards(t *testing.T) {
	for _, scenario := range []string{"complete", "incomplete", "duplicate", "invalid-time"} {
		pages := 0
		requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
			if strings.Contains(target, "cxxqkcbl") {
				return []byte(`{"code":"0","datas":{"cxxqkcbl":{"rows":[]}}}`), nil
			}
			pages++
			rows := []any{}
			count := 100
			if pages == 2 {
				count = 1
			}
			if scenario == "incomplete" && pages == 2 {
				count = 0
			}
			for index := 0; index < count; index++ {
				wid := fmt.Sprintf("W-%d-%d", pages, index)
				if scenario == "duplicate" {
					wid = "same"
				}
				start := "2000-01-01 00:00:00"
				if scenario == "invalid-time" {
					start = "unparseable"
				}
				rows = append(rows, map[string]any{"WID": wid, "XKLY": "01", "KSSJ": "2099-09-01 00:00:00", "DSXSKSSJ": "2099-09-08 00:00:00", "TKKSSJ": start})
			}
			return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"cxxsktrw": map[string]any{"rows": rows, "totalSize": 101}}})
		})
		courses, _, err := FetchOfficialWithdrawals(context.Background(), requester, "2099-2100-1", time.Now())
		if scenario == "incomplete" || scenario == "duplicate" {
			if err == nil {
				t.Fatal(scenario, "accepted incomplete/duplicate data")
			}
			continue
		}
		if err != nil || len(courses) != 101 || pages != 2 {
			t.Fatal(scenario, len(courses), pages, err)
		}
		if scenario == "invalid-time" && courses[0]["can_withdraw"] != false {
			t.Fatal("unparseable opening time allowed mutation")
		}
	}
}

func TestOfficialWithdrawalMalformedListIsNotEmpty(t *testing.T) {
	requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
		return []byte(`{"code":"0","datas":{"cxxsktrw":{}}}`), nil
	})
	if _, _, err := FetchOfficialWithdrawals(context.Background(), requester, "2099-2100-1", time.Now()); err == nil {
		t.Fatal("malformed list accepted")
	}
}
