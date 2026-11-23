package academic

import (
	"context"
	"errors"
	"net/url"
	"strings"
	"testing"
)

func TestMessagesPartialFailureAndReadState(t *testing.T) {
	requester := requestFunc(func(_ context.Context, _, target string, form url.Values) ([]byte, error) {
		if form.Get("pageSize") != "100" {
			t.Fatal(form)
		}
		if strings.HasSuffix(target, "cxshxxlb.do") {
			return nil, errors.New("unavailable")
		}
		return []byte(`{"code":"0","datas":{"cxtxxxlb":{"rows":[{"WID":"one","XXBT":" 培养计划更新 ","XXNR":"内容","YDZTDM":"02"}]}}}`), nil
	})
	result, err := FetchSystemMessages(context.Background(), requester)
	if err != nil || len(result.Messages) != 1 || !result.Messages[0].Read || result.Messages[0].Title != "培养计划更新" || len(result.FailedKinds) != 1 || result.FailedKinds[0] != "audit" {
		t.Fatalf("result: %+v %v", result, err)
	}
}

func TestMessagesMalformedBothTabsCannotClearCache(t *testing.T) {
	requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
		return []byte(`{"code":"0","datas":{}}`), nil
	})
	if _, err := FetchSystemMessages(context.Background(), requester); err == nil {
		t.Fatal("malformed empty response accepted")
	}
}
