package academic

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
)

func TestOutlineSearchUsesOfficialRulesAndStableFields(t *testing.T) {
	requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		if method != "POST" || !strings.HasSuffix(target, "modules/dgcx/cxlb.do") || form.Get("pageNumber") != "2" || form.Get("*order") != "+KCH" {
			t.Fatalf("request: %s %s %v", method, target, form)
		}
		var rules []map[string]string
		if err := json.Unmarshal([]byte(form.Get("querySetting")), &rules); err != nil || len(rules) != 2 || rules[0]["name"] != "KCM" || rules[1]["builder"] != "between" || rules[1]["value"] != "1,4" {
			t.Fatalf("rules: %+v %v", rules, err)
		}
		return []byte(`{"datas":{"cxlb":{"totalSize":22,"rows":[{"KCH":"TEST","KCM":"测试","KKDWDM_DISPLAY":"学院","XF":2,"WID":"not-public"}]}}}`), nil
	})
	result, err := SearchOutlines(context.Background(), requester, OutlineSearch{Keyword: "测试", Filters: map[string]any{"XF": []any{1.0, 4.0}}, Page: 2, PageSize: 20})
	if err != nil || result["total"] != 22 {
		t.Fatalf("result: %+v %v", result, err)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "not-public") {
		t.Fatal("internal identifier leaked")
	}
}

func TestOutlineOverviewPartialFailureRetainsOtherSections(t *testing.T) {
	requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
		switch {
		case strings.HasSuffix(target, "cshdgsj.do"):
			return []byte(`{"code":"0"}`), nil
		case strings.HasSuffix(target, "cxkcxxx.do"):
			return []byte(`{"rows":[{"KCM":"测试","CJJLFS_DISPLAY":"百分制","XF":3}]}`), nil
		case strings.HasSuffix(target, "cxkcdgxx.do"):
			return nil, errors.New("temporary failure")
		default:
			return []byte(`{"rows":[{"KCJJ":"课程简介"}]}`), nil
		}
	})
	result, err := OutlineOverview(context.Background(), requester, "TEST")
	if err != nil || result["course_name"] != "测试" || result["introduction"] != "课程简介" || result["failures"].(map[string]string)["outline"] != "unavailable" {
		t.Fatalf("partial: %+v %v", result, err)
	}
}

func TestOutlineQueryRejectsUnsupportedFilters(t *testing.T) {
	for _, filters := range []map[string]any{{"WID": "private"}, {"XF": []any{1.0}}, {"KCM": map[string]any{"raw": "value"}}} {
		if (OutlineSearch{Page: 1, PageSize: 20, Filters: filters}).Valid() {
			t.Fatalf("accepted: %+v", filters)
		}
	}
}
