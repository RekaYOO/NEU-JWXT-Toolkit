package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
)

type officialWithdrawalClient struct {
	testCampusClient
	writes                     int
	absent, uncertain, blocked bool
}

func (client *officialWithdrawalClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	switch {
	case strings.Contains(target, "cxdqxkxnxq"):
		return []byte(`{"code":"0","datas":{"cxdqxkxnxq":{"rows":[{"DM":"2099-2100-1","MC":"测试学期"}]}}}`), nil
	case strings.Contains(target, "cxxsktrw"):
		rows := []any{}
		if !client.absent {
			source := "01"
			if client.blocked {
				source = "02"
			}
			rows = append(rows, map[string]any{"WID": "W1", "KCH": "C1", "XKLY": source, "KSSJ": "2099-09-01 08:00:00", "DSXSKSSJ": "2099-09-08 08:00:00"})
		}
		return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"cxxsktrw": map[string]any{"rows": rows, "totalSize": len(rows)}}})
	case strings.Contains(target, "cxxqkcbl"):
		return []byte(`{"code":"0","datas":{"cxxqkcbl":{"rows":[]}}}`), nil
	case strings.HasSuffix(target, "tkglController/toTk.do"):
		client.writes++
		if method != "POST" || form.Get("XNXQDM") != "2099-2100-1" || form.Get("querySetting") != `[{"builder":"m_value_equal","linkOpt":"and","name":"WID","value":"W1"}]` {
			return nil, errors.New("bad mutation request")
		}
		if client.uncertain {
			return nil, errors.New("synthetic lost response")
		}
		client.absent = true
		return []byte(`{"code":"0","msg":"成功"}`), nil
	}
	return nil, errors.New("unexpected request")
}

func TestOfficialWithdrawalHTTPAndUnknownNeverReplay(t *testing.T) {
	for _, uncertain := range []bool{false, true} {
		api, handler := testApplication(t, "local-single")
		client := &officialWithdrawalClient{testCampusClient: testCampusClient{account: "student"}, uncertain: uncertain}
		api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
		cookie := loginCookie(t, handler, "student")
		base := "/api/course-selection/official-withdrawal"
		response := apiRequest(handler, "GET", base, "", cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), `"entry_status":"open"`) {
			t.Fatal(response.Code, response.Body.String())
		}
		if response := apiRequest(handler, "POST", base+"/deselect", `{"term_code":"2099-2100-1","wid":"W1"}`, cookie); response.Code != 422 || client.writes != 0 {
			t.Fatal("unconfirmed write")
		}
		if response := apiRequest(handler, "POST", base+"/deselect", `{"term_code":"2098-2099-1","wid":"W1","confirmed":true}`, cookie); response.Code != 409 || client.writes != 0 {
			t.Fatal("stale term write")
		}
		body := `{"term_code":"2099-2100-1","wid":"W1","confirmed":true}`
		client.blocked = true
		response = apiRequest(handler, "POST", base+"/deselect", body, cookie)
		if response.Code != 409 || client.writes != 0 {
			t.Fatal("non-self-selected course submitted")
		}
		client.blocked = false
		response = apiRequest(handler, "POST", base+"/deselect", body, cookie)
		expected := 200
		if uncertain {
			expected = 409
		}
		if response.Code != expected || client.writes != 1 {
			t.Fatal(response.Code, response.Body.String(), client.writes)
		}
		response = apiRequest(handler, "POST", base+"/deselect", body, cookie)
		if response.Code != expected || client.writes != 1 {
			t.Fatal("replayed write", response.Code, client.writes)
		}
		client.absent = true
		response = apiRequest(handler, "POST", base+"/deselect", body, cookie)
		if response.Code != 200 || client.writes != 1 {
			t.Fatal("failed read-only reconciliation", response.Code, client.writes)
		}
	}
}
