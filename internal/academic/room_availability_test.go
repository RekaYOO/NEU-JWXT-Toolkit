package academic

import (
	"context"
	"errors"
	"net/url"
	"strings"
	"testing"
)

func integerPointer(value int) *int { return &value }

func TestRoomAvailabilityUnknownAndSundayOrder(t *testing.T) {
	courses := []map[string]any{{"weekday": 7, "start_section": 1, "end_section": 2, "weeks": []int{1, 3}}}
	sunday := RoomSlot{Week: integerPointer(1), DayStart: integerPointer(7), DayEnd: integerPointer(1), Start: integerPointer(1), End: integerPointer(2)}
	if RoomFree(courses, []RoomSlot{sunday}) {
		t.Fatal("Sunday conflict ignored")
	}
	if !RoomFree(courses, []RoomSlot{{Week: integerPointer(2), Day: integerPointer(7)}}) {
		t.Fatal("nonmatching week blocked")
	}
	if !RoomFree(courses, []RoomSlot{sunday, {Day: integerPointer(2), Joiner: "or"}}) {
		t.Fatal("OR did not preserve available interval")
	}
	courses[0]["weeks"] = []int{}
	if RoomFree(courses, []RoomSlot{sunday}) {
		t.Fatal("unknown recurrence treated as free")
	}
	courses[0]["weekday"] = 9
	if RoomFree(courses, []RoomSlot{{Day: integerPointer(2)}}) {
		t.Fatal("invalid weekday treated as free")
	}
	invalid := RoomSlot{DayStart: integerPointer(1), DayEnd: integerPointer(7)}
	if invalid.normalize() {
		t.Fatal("reverse Sunday-first range accepted")
	}
}

func TestRoomScanLimitResumeAndNoRepeatedRoomFetch(t *testing.T) {
	queried := []string{}
	failure := errors.New("temporary schedule failure")
	requester := requestFunc(func(_ context.Context, _, target string, form url.Values) ([]byte, error) {
		if strings.HasSuffix(target, "jslb.do") {
			return []byte(`{"datas":{"jslb":{"totalSize":2,"pageSize":20,"rows":[{"CODE":"busy","JASMC":"占用教室"},{"CODE":"free","JASMC":"空闲教室"}]}}}`), nil
		}
		queried = append(queried, form.Get("CODE"))
		if form.Get("CODE") == "busy" {
			return []byte(`{"datas":{"arrangedList":[{"courseName":"课程","dayOfWeek":1,"beginSection":1,"endSection":2,"weeks":[1]}]}}`), nil
		}
		if len(queried) == 2 {
			return nil, failure
		}
		return []byte(`{"datas":{"arrangedList":[]}}`), nil
	})
	query := RoomAvailabilityQuery{Term: "2026-1", Campus: "all", Limit: 1, Slots: []RoomSlot{{Day: integerPointer(1), Week: integerPointer(1)}}}
	first, err := ScanAvailableRooms(context.Background(), requester, query)
	if err != nil || first["scanned"] != 1 || first["complete"] != false || first["total"] != 0 {
		t.Fatalf("first: %+v %v", first, err)
	}
	query.Cursor = first["cursor"].(int)
	query.Seen = first["scanned_room_ids"].([]string)
	failed, err := ScanAvailableRooms(context.Background(), requester, query)
	if err == nil || failed != nil {
		t.Fatalf("failed room query returned availability: %+v %v", failed, err)
	}
	second, err := ScanAvailableRooms(context.Background(), requester, query)
	if err != nil || second["total"] != 1 || second["complete"] != true || strings.Join(queried, ",") != "busy,free,free" {
		t.Fatalf("second: %+v %v requests=%v", second, err, queried)
	}
}
