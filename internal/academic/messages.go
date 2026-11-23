package academic

import (
	"context"
	"net/http"
	"net/url"
	"sort"
	"strings"
)

type SystemMessage struct {
	ID      string `json:"id"`
	SentAt  string `json:"sent_at"`
	Title   string `json:"title"`
	Content string `json:"content"`
	Read    bool   `json:"read"`
	AppName string `json:"app_name"`
	URL     string `json:"url"`
	Kind    string `json:"kind"`
}

type SystemMessages struct {
	Messages    []SystemMessage `json:"messages"`
	FailedKinds []string        `json:"failed_kinds"`
}

func SortMessages(messages []SystemMessage) {
	sort.SliceStable(messages, func(left, right int) bool {
		if messages[left].SentAt != messages[right].SentAt {
			return messages[left].SentAt > messages[right].SentAt
		}
		if messages[left].ID != messages[right].ID {
			return messages[left].ID > messages[right].ID
		}
		return messages[left].Kind < messages[right].Kind
	})
}

func FetchSystemMessages(ctx context.Context, requester Requester) (SystemMessages, error) {
	result := SystemMessages{Messages: []SystemMessage{}, FailedKinds: []string{}}
	var lastErr error
	for _, tab := range []struct{ kind, action string }{{"reminder", "cxtxxxlb"}, {"audit", "cxshxxlb"}} {
		datas, err := requestData(ctx, requester, http.MethodPost, "https://jwxt.neu.edu.cn/jwapp/sys/xxzxapp/modules/xtxx/"+tab.action+".do", url.Values{"pageNumber": {"1"}, "pageSize": {"100"}, "querySetting": {"[]"}})
		var rows []map[string]any
		if err == nil {
			_, _, err = outlineRows(map[string]any{"datas": datas}, tab.action)
			if err == nil {
				rows, err = reportRows(Object(datas[tab.action])["rows"])
			}
		}
		if err != nil {
			lastErr = err
			result.FailedKinds = append(result.FailedKinds, tab.kind)
			continue
		}
		for _, row := range rows {
			if id := Text(row["WID"]); id != "" {
				result.Messages = append(result.Messages, SystemMessage{ID: id, SentAt: Text(row["FSSJ"]), Title: strings.TrimSpace(Text(row["XXBT"])), Content: strings.TrimSpace(Text(row["XXNR"])), Read: Text(row["YDZTDM"]) == "02", AppName: strings.TrimSpace(Text(row["APPNAME"])), URL: strings.TrimSpace(firstText(row["YDURL"], row["PCURL"])), Kind: tab.kind})
			}
		}
	}
	if len(result.FailedKinds) == 2 {
		return result, lastErr
	}
	SortMessages(result.Messages)
	if len(result.Messages) > 200 {
		result.Messages = result.Messages[:200]
	}
	return result, nil
}
