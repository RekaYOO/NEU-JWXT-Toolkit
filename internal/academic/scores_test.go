package academic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

type requestFunc func(context.Context, string, string, url.Values) ([]byte, error)

func (request requestFunc) Request(ctx context.Context, method, target string, form url.Values) ([]byte, error) {
	return request(ctx, method, target, form)
}

func TestScoresPaginationAndPrivateReference(t *testing.T) {
	pages := 0
	requester := requestFunc(func(ctx context.Context, method, target string, form url.Values) ([]byte, error) {
		switch {
		case strings.HasSuffix(target, "cxwdcjxnxq.do"):
			return []byte(`{"code":"0","datas":{"cxwdcjxnxq":{"rows":[{"XNXQDM":"2025-2026-1"},{"XNXQDM":"2025-2026-1"}]}}}`), nil
		case strings.HasSuffix(target, "cxwdcj.do"):
			pages++
			if method != http.MethodPost || form.Get("XNXQDM") != "2025-2026-1" || form.Get("pageNumber") != string(rune('0'+pages)) {
				t.Fatalf("unexpected request: %s %v", method, form)
			}
			if pages == 1 {
				return []byte(`{"code":"0","datas":{"cxwdcj":{"totalSize":2,"rows":[{"KCM":"高数","KCH":"MATH","XSZCJ":"90","JD":" 4","XF":"4 ","SFJG_DISPLAY":"是","WID":"private-reference"}]}}}`), nil
			}
			return []byte(`{"code":"0","datas":{"cxwdcj":{"totalSize":2,"rows":[{"KCM":"英语","KCH":"ENG","XSZCJ":"59","JD":0,"XF":2,"SFJG_DISPLAY":"否"}]}}}`), nil
		default:
			if method != http.MethodGet || !strings.HasSuffix(target, "queryPjxfjd.do") {
				t.Fatalf("unexpected request: %s %s", method, target)
			}
			return []byte(`{"code":"0","datas":{"queryPjxfjd":{"ZPJXFJD":" 2.6667 ","ZPJXFJDPM":" 42 "}}}`), nil
		}
	})
	scores, err := FetchScores(context.Background(), requester)
	if err != nil || pages != 2 || len(scores.Scores) != 2 || scores.OverallGPA == nil || *scores.OverallGPA != 2.6667 {
		t.Fatalf("scores: %+v pages=%d error=%v", scores, pages, err)
	}
	if scores.OfficialMajorRank == nil || *scores.OfficialMajorRank != 42 || scores.Semantic().OfficialMajorRank == nil {
		t.Fatal("official rank must be preserved in the semantic cache")
	}
	encoded, _ := json.Marshal(scores.Semantic())
	if strings.Contains(string(encoded), "private-reference") || strings.Contains(string(encoded), "detail_ref") {
		t.Fatalf("private reference exposed: %s", encoded)
	}
	for _, score := range scores.Scores {
		if score.Code == "MATH" && (score.DetailRef == "" || !score.Passed || score.ScoreValue != 90 || score.Credit != 4 || score.GPA != 4) {
			t.Fatalf("wrong score: %+v", score)
		}
	}
}

func TestScoresIgnoreUnavailableOfficialRank(t *testing.T) {
	for _, value := range []string{`null`, `" "`, `"0"`, `"-1"`, `"12.5%"`, `"2147483648"`} {
		requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
			if strings.HasSuffix(target, "cxwdcjxnxq.do") {
				return []byte(`{"code":"0","datas":{"cxwdcjxnxq":{"rows":[]}}}`), nil
			}
			return []byte(`{"code":"0","datas":{"queryPjxfjd":{"ZPJXFJD":"3.0000","ZPJXFJDPM":` + value + `}}}`), nil
		})
		result, err := FetchScores(context.Background(), requester)
		if err != nil || result.OfficialMajorRank != nil || result.OverallGPA == nil || *result.OverallGPA != 3 {
			t.Fatalf("unavailable rank %s affected scores: %+v %v", value, result, err)
		}
	}
}

func TestScoresRejectMalformedAndAcceptExplicitEmpty(t *testing.T) {
	for _, response := range []string{`{"code":"0","datas":{}}`, `{"code":"0","datas":{"cxwdcjxnxq":{"rows":null}}}`, `{"code":"1","datas":{}}`, `<html>login</html>`} {
		requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) { return []byte(response), nil })
		if _, err := FetchScores(context.Background(), requester); err == nil {
			t.Fatalf("accepted malformed response: %s", response)
		}
	}
	requester := requestFunc(func(_ context.Context, _, target string, _ url.Values) ([]byte, error) {
		if strings.HasSuffix(target, "cxwdcjxnxq.do") {
			return []byte(`{"code":"0","datas":{"cxwdcjxnxq":{"rows":[]}}}`), nil
		}
		return []byte(`{"code":"0","datas":{"queryPjxfjd":{"ZPJXFJD":null}}}`), nil
	})
	result, err := FetchScores(context.Background(), requester)
	if err != nil || result.Scores == nil || len(result.Scores) != 0 {
		t.Fatalf("explicit empty: %+v %v", result, err)
	}
}
