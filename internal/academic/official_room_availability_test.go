package academic

import (
	"context"
	"fmt"
	"net/url"
	"strings"
	"testing"
)

const roomAvailabilityFixtureDate = "2026-09-09"

func officialRoomFixture(id, name string) string {
	return fmt.Sprintf(`{"JASDM":%q,"JASMC":%q,"XXXQDM":"00","JXLDM":"01","JASLXDM":"04","XXXQDM_DISPLAY":"示例校区","KXRQ":%q,"KSJC":null,"JSJC":null,"KXJC":"第三节-第四节","KXSJ":"10:00-11:40"}`, id, name, roomAvailabilityFixtureDate)
}

func officialRoomTable(total, size int, rows ...string) []byte {
	return []byte(fmt.Sprintf(`{"code":"0","datas":{"cxkxjs":{"totalSize":%d,"pageSize":%d,"rows":[%s]}}}`, total, size, strings.Join(rows, ",")))
}

func TestRoomOfficialAvailabilityUsesSchoolRowsAndCatalogIntersection(t *testing.T) {
	for _, extraFilter := range []bool{false, true} {
		query := OfficialRoomQuery{Term: "2026-2027-1", Date: roomAvailabilityFixtureDate, Start: 3, End: 4, Keyword: "示例", Filters: map[string]any{"campus": "00", "building": "01", "room_type": "04"}}
		if extraFilter {
			query.Filters["min_capacity"] = 40
		}
		calls := 0
		requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
			calls++
			if calls == 1 {
				if method != "POST" || !strings.HasSuffix(target, "/kxjas/modules/kxjscxxs/cxkxjs.do") || form.Get("KXRQ") != query.Date || form.Get("KSJC") != "3" || form.Get("JSJC") != "4" || form.Get("JASMC") != "示例" || form.Get("JASLXDM") != "04" || strings.Contains(form.Get("querySetting"), "capacity") {
					t.Fatalf("incorrect official query: %s %v", target, form)
				}
				return officialRoomTable(2, 1, officialRoomFixture("demo-1", "示例一"), officialRoomFixture("demo-2", "示例二")), nil
			}
			if !extraFilter || !strings.HasSuffix(target, "/jslb.do") || !strings.Contains(form.Get("querySetting"), `"name":"RL"`) {
				t.Fatalf("unexpected catalog query: %s %v", target, form)
			}
			return []byte(`{"datas":{"jslb":{"totalSize":3,"pageSize":20,"rows":[{"CODE":"demo-1","JASMC":"示例一","XXXQDM":"00","JXLDM":"01","JASLXDM":"04","RL":30},{"CODE":"demo-2","JASMC":"示例二","XXXQDM":"00","JXLDM":"01","JASLXDM":"04","RL":50},{"CODE":"demo-3","JASMC":"示例三","XXXQDM":"00","JXLDM":"01","JASLXDM":"04","RL":60}]}}}`), nil
		})
		rooms, err := FetchOfficialAvailableRooms(context.Background(), requester, query)
		if err != nil {
			t.Fatal(err)
		}
		if !extraFilter && (calls != 1 || len(rooms) != 2) {
			t.Fatalf("official non-paged response: %+v, %d requests", rooms, calls)
		}
		if extraFilter && (calls != 2 || len(rooms) != 1 || rooms[0]["id"] != "demo-2" || rooms[0]["details"].(map[string]string)["capacity"] != "50") {
			t.Fatalf("catalog result must not add rooms the school did not report free: %+v, %d requests", rooms, calls)
		}
		if rooms[0]["availability"].(map[string]string)["time"] != "10:00-11:40" {
			t.Fatal("official time display was lost")
		}
	}
}

func TestRoomOfficialAvailabilityDistinguishesEmptyBrokenAndPaged(t *testing.T) {
	query := OfficialRoomQuery{Term: "2026-2027-1", Date: roomAvailabilityFixtureDate, Start: 3, End: 4}
	for _, scenario := range []struct {
		name  string
		pages [][]byte
		want  int
	}{
		{"empty", [][]byte{officialRoomTable(0, 15)}, 0},
		{"missing", [][]byte{[]byte(`{"code":0,"datas":{"cxkxjs":{"totalSize":0}}}`)}, -1},
		{"mismatched date", [][]byte{officialRoomTable(1, 15, strings.Replace(officialRoomFixture("demo-1", "示例一"), roomAvailabilityFixtureDate, "2026-09-10", 1))}, -1},
		{"paged", [][]byte{officialRoomTable(2, 1, officialRoomFixture("demo-1", "示例一")), officialRoomTable(2, 1, officialRoomFixture("demo-2", "示例二"))}, 2},
		{"repeated page", [][]byte{officialRoomTable(2, 1, officialRoomFixture("demo-1", "示例一")), officialRoomTable(2, 1, officialRoomFixture("demo-1", "示例一"))}, -1},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			calls := 0
			rooms, err := FetchOfficialAvailableRooms(context.Background(), requestFunc(func(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
				calls++
				if calls > len(scenario.pages) || form.Get("pageNumber") != fmt.Sprint(calls) {
					t.Fatalf("unexpected page %d (%s)", calls, form.Get("pageNumber"))
				}
				return scenario.pages[calls-1], nil
			}), query)
			if scenario.want < 0 && (err == nil || rooms != nil) || scenario.want >= 0 && (err != nil || rooms == nil || len(rooms) != scenario.want) {
				t.Fatalf("results: %+v, %v", rooms, err)
			}
		})
	}
}
