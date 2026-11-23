package academic

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKFilterOptionsIncludesMeetingCampusesAndCategories(t *testing.T) {
	courses := []map[string]any{{"course_category": "专业课", "course_categories": []any{"专业课", "通识选修类"}, "campus": "00", "schedules": []any{map[string]any{"campus": "01"}}, "department": "学院"}, {"course_category": "专业课", "department": "学院"}}
	result := JWXKFilterOptions(courses, nil, "00", "")
	if len(result["campuses"].([]map[string]string)) != 2 || len(result["course_categories"].([]map[string]string)) != 2 || len(result["departments"].([]map[string]string)) != 1 || len(result["sections"].([]map[string]string)) != 30 || len(result["weekdays"].([]map[string]string)) != 7 {
		t.Fatal(result)
	}
}

func TestJWXKFilterOptionsOnlyReadsOneBusinessPage(t *testing.T) {
	reads := 0
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		switch input.Path {
		case "/xsxk/web/now":
			return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
		case "/xsxk/web/studentInfo", "/xsxk/elective/user":
			return []byte(`{"code":200,"data":{"student":{"campus":"00","electiveBatchList":[{"code":"round","name":"轮次","clazzTypeList":["ALLKC","XGKC"]}]}}}`), nil
		case "/xsxk/elective/clazz/list":
			reads++
			var body map[string]any
			json.Unmarshal(input.JSON, &body)
			if body["teachingClassType"] != "XGKC" || Number(body["pageNumber"]) != 1 || Number(body["pageSize"]) != 50 {
				t.Fatal(body)
			}
			return []byte(`{"code":200,"data":{"total":100,"rows":[{"KCH":"C","KCLB":"通识选修类"}]}}`), nil
		}
		t.Fatal(input)
		return nil, nil
	})
	result, err := FetchJWXKFilterOptions(context.Background(), requester, "direct", "round")
	if err != nil || reads != 1 || len(result["scopes"].([]map[string]string)) != 4 {
		t.Fatal(result, err, reads)
	}
}
