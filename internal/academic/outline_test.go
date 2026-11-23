package academic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

func TestOutlineMetadataKeepsOnlyAllowedFields(t *testing.T) {
	calls := 0
	requester := requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		calls++
		if method != http.MethodPost || form.Get("KCH") != "TEST.01" {
			t.Fatalf("request: %s %v", method, form)
		}
		if calls == 1 {
			if !strings.HasSuffix(target, "cshdgsj.do") {
				t.Fatal(target)
			}
			return []byte(`{"code":"0"}`), nil
		}
		if !strings.HasSuffix(target, "cxkcxxx.do") {
			t.Fatal(target)
		}
		return []byte(`{"code":"0","datas":{"cxkcxxx":{"rows":[{"KCM":"测试课程","CJJLFS":"02","CJJLFS_DISPLAY":"两级制","KSLXDM_DISPLAY":"考查","BBWID":"version","KCJJ":"must-not-persist","SYJC":"must-not-persist"}]}}}`), nil
	})
	metadata, err := FetchOutlineMetadata(context.Background(), requester, "TEST.01")
	if err != nil || calls != 2 || metadata["status"] != "success" || metadata["grading_scale"] != "两级制" {
		t.Fatalf("metadata: %+v %v", metadata, err)
	}
	encoded, _ := json.Marshal(metadata)
	if strings.Contains(string(encoded), "must-not-persist") {
		t.Fatal("outline body crossed persistence boundary")
	}
}

func TestOutlineEmptyIsDifferentFromMalformed(t *testing.T) {
	for _, fixture := range []struct {
		body  string
		valid bool
	}{{`{"rows":[]}`, true}, {`{"datas":{"cxkcxxx":{"rows":[]}}}`, true}, {`{"rows":null}`, false}, {`{"code":"1","rows":[]}`, false}, {`{"rows":[null]}`, false}, {`{"datas":{}}`, false}} {
		requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
			if strings.HasSuffix(target, "cshdgsj.do") {
				return []byte(`{"code":"0"}`), nil
			}
			return []byte(fixture.body), nil
		})
		metadata, err := FetchOutlineMetadata(context.Background(), requester, "TEST")
		if fixture.valid {
			if err != nil || metadata["status"] != "not_found" {
				t.Fatalf("empty: %+v %v", metadata, err)
			}
		} else if err == nil {
			t.Fatalf("accepted malformed: %s", fixture.body)
		}
	}
}
