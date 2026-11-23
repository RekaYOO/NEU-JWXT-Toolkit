package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strconv"
	"strings"
	"testing"
)

func TestResearchTopicsRequireCompleteStablePages(t *testing.T) {
	for _, scenario := range []string{"complete", "changed-total", "duplicate", "early-end", "missing-total", "wrong-batch", "malformed"} {
		t.Run(scenario, func(t *testing.T) {
			calls := 0
			requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
				calls++
				if method != "POST" || !strings.HasSuffix(target, "/kyxl/ktbmcxlb.do") || form.Get("pageSize") != "50" || !strings.Contains(form.Get("querySetting"), `"value":"batch"`) {
					t.Fatal("incorrect official request", target, form)
				}
				page, _ := strconv.Atoi(form.Get("pageNumber"))
				rows := []any{}
				count := 50
				if page == 2 {
					count = 1
					if scenario == "early-end" {
						count = 0
					}
				}
				for index := 0; index < count; index++ {
					identifier := strconv.Itoa((page-1)*50 + index)
					if scenario == "duplicate" && page == 2 {
						identifier = "0"
					}
					batch := "batch"
					if scenario == "wrong-batch" {
						batch = "other"
					}
					rows = append(rows, map[string]any{"WID": identifier, "PCWID": batch, "YJTM": "课题", "ZSXSS": 2, "YBMRS": 2, "BMZT": "10", "BMWID": "registration"})
				}
				container := map[string]any{"rows": rows, "totalSize": 51}
				if scenario == "changed-total" && page == 2 {
					container["totalSize"] = 52
				}
				if scenario == "missing-total" {
					delete(container, "totalSize")
				}
				if scenario == "malformed" {
					container["rows"] = []any{nil}
				}
				return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"ktbmcxlb": container}})
			})
			rows, err := FetchResearchTopics(context.Background(), requester, "batch")
			if scenario == "complete" {
				if err != nil || len(rows) != 51 || calls != 2 || rows[0]["can_enroll"] != false || rows[0]["can_cancel"] != true {
					t.Fatal(rows, calls, err)
				}
			} else if err == nil || rows != nil {
				t.Fatal("partial snapshot accepted", rows, err)
			}
		})
	}
}

func TestResearchEligibilityRankAndMissingData(t *testing.T) {
	for _, scenario := range []struct {
		body      string
		available bool
		rank      any
	}{
		{`{"code":"0","datas":{"cxxsjdjzypm":{"rows":[{"PJJD":"3.5","ZYPM":".2593"}]}}}`, true, "25.93"},
		{`{"code":"0","datas":{"cxxsjdjzypm":[]}}`, false, nil},
	} {
		requester := requestFunc(func(_ context.Context, _, _ string, form url.Values) ([]byte, error) {
			if form.Get("PCWID") != "batch" {
				t.Fatal("wrong eligibility batch")
			}
			return []byte(scenario.body), nil
		})
		result, err := FetchResearchEligibility(context.Background(), requester, "batch")
		if err != nil || result["available"] != scenario.available || result["major_rank"] != scenario.rank {
			t.Fatal(result, err)
		}
	}
	for _, body := range []string{`{"code":"1"}`, `{"code":"0","datas":{}}`, `{"code":"0","datas":{"cxxsjdjzypm":{}}}`} {
		requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) { return []byte(body), nil })
		if _, err := FetchResearchEligibility(context.Background(), requester, "batch"); err == nil {
			t.Fatal("failed eligibility treated as unavailable")
		}
	}
}

func TestResearchConfirmedKeepsOfficialIdentityAndOptionalFields(t *testing.T) {
	requester := requestFunc(func(_ context.Context, _, target string, form url.Values) ([]byte, error) {
		if strings.HasSuffix(target, "yqrktcxlb.do") {
			return []byte(`{"code":"0","datas":{"yqrktcxlb":{"totalSize":1,"rows":[{"WID":"confirmed-record","KTWID":"archived-topic","PCWID":"batch","YJTM":"已归档课题","ZJS":3,"QRZT_DISPLAY":"已确认","XXZJFJ":{"token":"synthetic"},"SZDWDM_DISPLAY":"学院","SFZDSYS":"1","SSZDSYS_DISPLAY":"实验室"}]}}}`), nil
		}
		if !strings.HasSuffix(target, "xszjcxlb.do") || form.Get("BMWID") != "confirmed-record" {
			t.Fatal("journal used topic id instead of confirmed record", target, form)
		}
		return []byte(`{"code":"0","datas":{"xszjcxlb":{"rows":[{"WID":"journal","BMWID":"confirmed-record","XLNR":"实验记录","PYZT":"10","FJ":"synthetic-attachment","PX":1}]}}}`), nil
	})
	rows, err := FetchResearchConfirmed(context.Background(), requester, "batch")
	if err != nil || len(rows) != 1 || rows[0]["record_id"] != "confirmed-record" || rows[0]["topic_id"] != "archived-topic" || rows[0]["has_attachment"] != true || rows[0]["department"] != "学院" || rows[0]["journal_count"] != 3 {
		t.Fatal(rows, err)
	}
	journals, err := FetchResearchJournals(context.Background(), requester, Text(rows[0]["record_id"]))
	if err != nil || len(journals) != 1 || journals[0]["status"] != "10" || journals[0]["content"] != "实验记录" {
		t.Fatal(journals, err)
	}
}

func TestResearchSnapshotRejectsBatchChangeAndConfirmedFailure(t *testing.T) {
	for _, scenario := range []string{"complete", "changed-batch", "confirmed-failed"} {
		t.Run(scenario, func(t *testing.T) {
			batchReads := 0
			requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
				switch {
				case strings.HasSuffix(target, "cxdqxnxqpc.do"):
					batchReads++
					identifier := "batch"
					if scenario == "changed-batch" && batchReads == 2 {
						identifier = "next"
					}
					return json.Marshal(map[string]any{"code": "0", "datas": map[string]any{"cxdqxnxqpc": map[string]any{"WID": identifier, "ZYPM": ".3", "XSJD": "3.0"}}})
				case strings.HasSuffix(target, "cxxsjdjzypm.do"):
					return []byte(`{"code":"0","datas":{"cxxsjdjzypm":[]}}`), nil
				case strings.HasSuffix(target, "ktbmcxlb.do"):
					return []byte(`{"code":"0","datas":{"ktbmcxlb":{"rows":[],"totalSize":0}}}`), nil
				case strings.HasSuffix(target, "yqrktcxlb.do"):
					if scenario == "confirmed-failed" {
						return []byte(`{"code":"1"}`), nil
					}
					return []byte(`{"code":"0","datas":{"yqrktcxlb":[]}}`), nil
				}
				t.Fatal("unexpected request", target)
				return nil, nil
			})
			payload, err := FetchResearchSnapshot(context.Background(), requester)
			if scenario == "complete" {
				if err != nil || payload["total"] != 0 || Object(payload["batch"])["rank_limit_percent"] != float64(30) {
					t.Fatal(payload, err)
				}
			} else if err == nil || payload != nil {
				t.Fatal("partial or mixed-batch snapshot accepted", payload, err)
			}
		})
	}
}
