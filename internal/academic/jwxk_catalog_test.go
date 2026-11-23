package academic

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKCatalogMergeAndLocalCategoryFilter(t *testing.T) {
	reads := 0
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		switch input.Path {
		case "/xsxk/web/now":
			return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
		case "/xsxk/web/studentInfo", "/xsxk/elective/user":
			return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","name":"测试轮次","typeCode":"04","clazzTypeList":["FANKC","XGKC","ALLKC"]}]}}}`), nil
		case "/xsxk/elective/clazz/list":
			reads++
			var body map[string]any
			json.Unmarshal(input.JSON, &body)
			if body["teachingClassType"] == "ALLKC" || body["KCLB"] != nil {
				t.Fatal(body)
			}
			return []byte(`{"code":200,"data":{"total":1,"rows":[{"KCH":"CS1","KCM":"课程","KCLB":"专业方向类","tcList":[{"JXBID":"A","KRL":10,"YXRS":2,"QZXKRS":12},{"JXBID":"B","KRL":10,"YXRS":5}]}]}}`), nil
		}
		t.Fatal(input.Path)
		return nil, nil
	})
	result, err := SearchJWXKCatalog(context.Background(), requester, "direct", JWXKCatalogSearch{Batch: "round", Scope: "ALL", Page: 1, Size: 20, OnlineMode: "all", Filters: map[string]string{"KCLB": "专业方向课"}})
	if err != nil || reads != 2 || result["total"] != 1 {
		t.Fatal(result, err, reads)
	}
	groups := result["groups"].([]map[string]any)
	if len(groups) != 1 || groups[0]["class_count"] != 2 || len(jwxkStrings(groups[0]["source_tags"])) != 2 {
		t.Fatal(groups)
	}
	for _, course := range groups[0]["classes"].([]map[string]any) {
		if len(jwxkStrings(course["source_scopes"])) != 2 || course["full"] != false {
			t.Fatal(course)
		}
		if course["class_id"] == "A" && course["capacity"] != 8 {
			t.Fatal("capacity subtracted twice", course)
		}
	}
}

func TestJWXKGroupsStableIdentityAndCounts(t *testing.T) {
	rows := []map[string]any{
		{"course_code": "CS1", "course_name": "课程", "capacity": 10, "selected_count": 2, "eligibility_status": "selectable", "teacher": "甲", "campus": "00"},
		{"course_code": "cs1", "course_name": "另一显示名", "capacity": 10, "selected_count": 10, "eligibility_status": "unknown", "full": true, "teacher": "乙", "campus": "01"},
	}
	groups := GroupJWXKCourses(rows)
	if len(groups) != 1 || groups[0]["selectable_count"] != 1 || groups[0]["eligibility_pending_count"] != 1 || groups[0]["available_count"] != 1 || len(jwxkStrings(groups[0]["campuses"])) != 2 {
		t.Fatal(groups)
	}
	if groups[0]["group_id"] != jwxkID("jwxk-course-", "cs1") || len(Text(groups[0]["group_id"])) != len("jwxk-course-")+20 {
		t.Fatal(groups)
	}
}

func TestJWXKCatalogMeetingAndOnlineFilters(t *testing.T) {
	course := map[string]any{"course_name": "在线式课程", "campus": "00", "schedules": []map[string]any{{"weekday": 2, "start_section": 3, "end_section": 4, "campus": "01"}}}
	input := JWXKCatalogSearch{OnlineMode: "online", Campus: "浑南校区", TimeSlot: &JWXKTimeSlot{Weekday: 2, Section: 4}}
	if !matchesJWXKCatalog(course, input) {
		t.Fatal("meeting campus not considered")
	}
	input.TimeSlot.Section = 5
	if matchesJWXKCatalog(course, input) {
		t.Fatal("nonoverlapping section included")
	}
	input.TimeSlot = nil
	input.OnlineMode = "offline"
	if matchesJWXKCatalog(course, input) {
		t.Fatal("online course in offline filter")
	}
}

func TestJWXKCatalogScansBeforeLocalPagination(t *testing.T) {
	for _, oversized := range []bool{false, true} {
		reads := 0
		requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
			if input.Path == "/xsxk/web/now" {
				return []byte(`{"code":200,"data":{"currentTime":1794800000000}}`), nil
			}
			if input.Path != "/xsxk/elective/clazz/list" {
				return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","name":"测试轮次","typeCode":"02","clazzTypeList":["FANKC"]}]}}}`), nil
			}
			reads++
			var body map[string]any
			json.Unmarshal(input.JSON, &body)
			if int(Number(body["pageNumber"])) != reads || Number(body["pageSize"]) != 50 {
				t.Fatal(body)
			}
			total, count := 51, 50
			if oversized {
				total = 3001
			} else if reads == 2 {
				count = 1
			}
			rows := []map[string]any{}
			for index := 0; index < count; index++ {
				name := "普通课程"
				if reads == 2 && index == 0 {
					name = "在线式课程"
				}
				rows = append(rows, map[string]any{"KCH": fmt.Sprintf("C%d-%d", reads, index), "JXBID": fmt.Sprintf("T%d-%d", reads, index), "KCM": name})
			}
			return json.Marshal(map[string]any{"code": 200, "data": map[string]any{"total": total, "rows": rows}})
		})
		result, err := SearchJWXKCatalog(context.Background(), requester, "direct", JWXKCatalogSearch{Batch: "round", Scope: "FANKC", Page: 1, Size: 1, OnlineMode: "online"})
		if oversized {
			if err == nil || result != nil || reads != 60 {
				t.Fatal("truncated catalog reported as complete", result, err, reads)
			}
		} else if err != nil || reads != 2 || result["total"] != 1 || len(result["groups"].([]map[string]any)) != 1 {
			t.Fatal(result, err, reads)
		}
	}
}
