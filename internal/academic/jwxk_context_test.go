package academic

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type serviceRequestFunc func(context.Context, campus.ServiceRequest) ([]byte, error)

func (request serviceRequestFunc) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	return request(ctx, input)
}

func TestJWXKContextOfficialClockAndMenuFallback(t *testing.T) {
	official := time.Date(2026, 8, 15, 9, 0, 0, 0, time.FixedZone("CST", 8*3600))
	calls := 0
	requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
		calls++
		if input.Mode != "webvpn" || !input.ReadOnly || input.Service != "jwxk" {
			t.Fatal(input)
		}
		switch input.Path {
		case "/xsxk/web/now":
			return json.Marshal(map[string]any{"code": 200, "data": map[string]any{"currentTime": official.UnixMilli(), "onlineCount": 12}})
		case "/xsxk/web/studentInfo":
			return []byte(`{"code":200,"data":{"student":{"campus":"浑南校区","name":"private name","electiveBatchList":[{"code":"round","name":"抢选","beginTime":"2026-08-15 08:00:00","endTime":"2026-08-15 12:00:00","canSelect":"1","needConfirm":"1","typeCode":"02"}]}}}`), nil
		case "/xsxk/profile/index.html":
			return []byte(`<script>loginVue.batchList = [{"code":"round","name":"公开轮次","clazzTypeList":["FANKC"],"canSelect":"0"}];</script>`), nil
		}
		t.Fatal(input.Path)
		return nil, nil
	})
	result, err := FetchJWXKContext(context.Background(), requester, "webvpn")
	if err != nil {
		t.Fatal(err)
	}
	batch := result["batches"].([]map[string]any)[0]
	if calls != 3 || batch["state"] != "active" || batch["can_enter"] != true || batch["name"] != "抢选" || batch["access_scope"] != "account" || batch["need_confirm"] != true {
		t.Fatal(result)
	}
	if batch["menus"].([]map[string]string)[0]["name"] != "培养方案内课" || result["current_campus"] != "01" {
		t.Fatal(result)
	}
	if _, exists := result["name"]; exists {
		t.Fatal("personal profile escaped projection")
	}
}

func TestJWXKUnknownTimeCannotEnableRound(t *testing.T) {
	row := map[string]any{"code": "round", "name": "轮次", "beginTime": "2020-01-01 00:00:00", "endTime": "2030-01-01 00:00:00", "canSelect": "1", "menuList": []any{map[string]any{"teachingClassType": "XGKC"}}}
	batch, err := jwxkBatch(row, time.Time{})
	if err != nil || batch["state"] != "unknown" || batch["can_enter"] != false {
		t.Fatal(batch, err)
	}
	if batch["menus"].([]map[string]string)[0]["name"] != "通识选修课" {
		t.Fatal(batch)
	}
	requester := serviceRequestFunc(func(context.Context, campus.ServiceRequest) ([]byte, error) {
		return []byte(`{"code":200,"data":{}}`), nil
	})
	if _, err := FetchJWXKContext(context.Background(), requester, "direct"); err == nil {
		t.Fatal("missing student treated as empty valid context")
	}
}
