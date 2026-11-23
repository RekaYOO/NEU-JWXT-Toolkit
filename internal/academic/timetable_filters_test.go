package academic

import (
	"context"
	"net/url"
	"testing"
)

func TestTimetableFiltersScanEveryPageAndPreserveCascadeRelations(t *testing.T) {
	calls := 0
	requester := requestFunc(func(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
		calls++
		if form.Get("pageSize") != "1000" {
			t.Fatal(form)
		}
		if form.Get("pageNumber") == "1" {
			return []byte(`{"datas":{"jslb":{"pageNumber":1,"pageSize":2,"totalSize":3,"rows":[{"CODE":"1","JASMC":"教室1","XXXQDM":"A","JXLDM":"X","JXLDM_DISPLAY":"教学楼X"},{"CODE":"2","JASMC":"教室2","XXXQDM":"A","JXLDM":"X","JXLDM_DISPLAY":"教学楼X"}]}}}`), nil
		}
		return []byte(`{"datas":{"jslb":{"pageNumber":2,"pageSize":2,"totalSize":3,"rows":[{"CODE":"3","JASMC":"教室3","XXXQDM":"B","JXLDM":"Y","JXLDM_DISPLAY":"教学楼Y"}]}}}`), nil
	})
	result, err := FetchTimetableFilters(context.Background(), requester, TimetableFilterQuery{Mode: "room", Term: "2026-1", Keys: []string{"building"}})
	if err != nil || calls != 2 {
		t.Fatalf("scan: %+v %v calls=%d", result, err, calls)
	}
	options := result["options"].(map[string][]map[string]string)
	if len(options) != 1 || len(options["building"]) != 2 {
		t.Fatal(options)
	}
	relations := result["relations"].([]map[string]string)
	if len(relations) != 2 || relations[0]["campus"] == "" || relations[1]["campus"] == "" {
		t.Fatal("parent cascade fields lost", relations)
	}
}

func TestTimetableFiltersRejectIncompleteAndRepeatedPages(t *testing.T) {
	for _, second := range []string{`{"pageNumber":1,"pageSize":1,"totalSize":2,"rows":[{"CODE":"1"}]}`, `{"pageNumber":2,"pageSize":1,"totalSize":2,"rows":[]}`, `{"pageNumber":2,"pageSize":1,"totalSize":2,"rows":[{"CODE":"1"}]}`} {
		requester := requestFunc(func(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
			body := `{"pageNumber":1,"pageSize":1,"totalSize":2,"rows":[{"CODE":"1"}]}`
			if form.Get("pageNumber") == "2" {
				body = second
			}
			return []byte(`{"datas":{"jslb":` + body + `}}`), nil
		})
		if _, err := FetchTimetableFilters(context.Background(), requester, TimetableFilterQuery{Mode: "room", Term: "2026-1"}); err == nil {
			t.Fatalf("incomplete directory accepted: %s", second)
		}
	}
}
