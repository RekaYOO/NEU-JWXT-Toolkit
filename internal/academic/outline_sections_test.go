package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"
	"testing"
)

func TestOutlineAssessmentResolvesRelationKeys(t *testing.T) {
	requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
		switch {
		case strings.HasSuffix(target, "cxkhxs.do"):
			return []byte(`{"rows":[{"WID":"private-type","KHXSMC":"期末考试"}]}`), nil
		case strings.HasSuffix(target, "cxkhhjsz.do"):
			return []byte(`{"rows":[{"WID":"private-link","KCMB":"掌握基础知识"}]}`), nil
		case strings.HasSuffix(target, "cxkhxscjzb.do"):
			return []byte(`{"rows":[{"KHXSWID":"private-type","KHHJWID":"private-link","CJZB":60}]}`), nil
		case strings.HasSuffix(target, "cxkccjpdff.do"):
			return []byte(`{"rows":[{"KCCJPDFF":"综合评定"}]}`), nil
		default:
			return []byte(`{"rows":[]}`), nil
		}
	})
	result, err := OutlineSections(context.Background(), requester, "TEST", "assessment")
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(result)
	for _, text := range []string{"期末考试", "掌握基础知识", "考核矩阵", "综合评定"} {
		if !strings.Contains(string(encoded), text) {
			t.Fatalf("missing %s: %s", text, encoded)
		}
	}
	if strings.Contains(string(encoded), "private-") {
		t.Fatal("relation IDs leaked")
	}
}

func TestOutlineTeachingLabelsAndPartialFailure(t *testing.T) {
	requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
		if strings.HasSuffix(target, "cxkcmbhnrdgx.do") {
			return []byte(`{"rows":[{"JXNR":"教学内容示例","QTXS1":2,"QTXS2":0,"WID":"private"}]}`), nil
		}
		if strings.HasSuffix(target, "cxkcmbxx.do") {
			return []byte(`{"broken":true}`), nil
		}
		return []byte(`{"rows":[]}`), nil
	})
	result, err := OutlineSections(context.Background(), requester, "TEST", "teaching")
	if err != nil || result["failures"].(map[string]string)["objectives"] != "invalid_response" {
		t.Fatalf("partial failure: %+v %v", result, err)
	}
	encoded, _ := json.Marshal(result)
	if !strings.Contains(string(encoded), "其他学时") || strings.Contains(string(encoded), "private") {
		t.Fatalf("presentation: %s", encoded)
	}
}
