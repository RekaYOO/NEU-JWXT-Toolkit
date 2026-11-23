package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const messagesTTL = 10 * time.Minute

func (api *API) registerMessagesResource() {
	api.resources.Register("system-messages", ResourceSpec{TTL: messagesTTL, Offline: true, PublicRefresh: true, Fetch: func(ctx context.Context, runtime *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		messages, err := academic.FetchSystemMessages(ctx, runtime)
		if err != nil {
			return nil, nil, err
		}
		if len(messages.FailedKinds) > 0 {
			session := store.Session{Account: runtime.Client.Account(), Generation: runtime.Generation}
			if entry, err := api.database.ReadCache(ctx, session, "system-messages", "default"); err == nil {
				var previous academic.SystemMessages
				if json.Unmarshal(entry.Payload, &previous) == nil {
					for _, message := range previous.Messages {
						for _, kind := range messages.FailedKinds {
							if message.Kind == kind {
								messages.Messages = append(messages.Messages, message)
								break
							}
						}
					}
				}
			}
		}
		academic.SortMessages(messages.Messages)
		payload, err := json.Marshal(messages)
		return payload, nil, err
	}})
}

func (api *API) registerMessages(router *http.ServeMux) {
	router.HandleFunc("POST /api/system-messages/read", api.markMessagesRead)
	router.HandleFunc("GET /api/system-messages/cache", api.cachedMessages)
	router.HandleFunc("GET /api/offline/system-messages", api.offlineHandler(api.cachedMessages))
}

func (api *API) markMessagesRead(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Messages []struct {
			ID   string `json:"message_id"`
			Kind string `json:"kind"`
		} `json:"messages"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if len(input.Messages) < 1 || len(input.Messages) > 20 {
		writeJSON(response, 422, map[string]string{"detail": "消息数量无效"})
		return
	}
	for index := range input.Messages {
		item := &input.Messages[index]
		item.ID = strings.TrimSpace(item.ID)
		if item.Kind == "" {
			item.Kind = "reminder"
		}
		if item.ID == "" || len(item.ID) > 128 || (item.Kind != "reminder" && item.Kind != "audit") {
			writeJSON(response, 422, map[string]string{"detail": "消息编号或类型无效"})
			return
		}
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 90*time.Second)
	defer cancel()
	release, err := api.resources.acquireAccount(ctx, session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	marked, failed := []map[string]string{}, []map[string]string{}
	seen := map[string]bool{}
	for _, item := range input.Messages {
		key := item.Kind + "\x00" + item.ID
		if seen[key] {
			continue
		}
		seen[key] = true
		kind := "txxx"
		if item.Kind == "audit" {
			kind = "shxx"
		}
		encoded, err := runtime.Request(ctx, http.MethodPost, "https://jwxt.neu.edu.cn/jwapp/sys/xxzxapp/api/xtxx/"+kind+"/updateReadAndGetMessage.do", url.Values{"WID": {item.ID}})
		if err == nil {
			payload, decodeErr := academic.Decode(encoded)
			if decodeErr != nil {
				err = decodeErr
			} else if academic.Text(payload["code"]) != "0" {
				err = errors.New("message acknowledgement rejected")
			}
		}
		if err != nil {
			failed = append(failed, map[string]string{"message_id": item.ID, "kind": item.Kind, "error": "未确认已读成功，请刷新核对"})
		} else {
			marked = append(marked, map[string]string{"message_id": item.ID, "kind": item.Kind})
		}
	}
	if len(marked) == 0 {
		writeJSON(response, 502, map[string]any{"detail": "教务系统消息已读同步未确认", "failed": failed})
		return
	}
	cacheUpdated := true
	if entry, err := api.database.ReadCache(ctx, session, "system-messages", "default"); err == nil {
		var payload academic.SystemMessages
		if json.Unmarshal(entry.Payload, &payload) == nil {
			for index := range payload.Messages {
				for _, item := range marked {
					if payload.Messages[index].ID == item["message_id"] && payload.Messages[index].Kind == item["kind"] {
						payload.Messages[index].Read = true
					}
				}
			}
			encoded, _ := json.Marshal(payload)
			digest := sha256.Sum256(encoded)
			entry.Payload, entry.Revision = encoded, "v1:"+hex.EncodeToString(digest[:])
			entry.PreserveFreshness = true
			_, err = api.database.CommitCache(ctx, session, entry, "mark_read", nil)
			cacheUpdated = err == nil
		} else {
			cacheUpdated = false
		}
	} else if !errors.Is(err, store.ErrMissing) {
		cacheUpdated = false
	}
	writeJSON(response, 200, map[string]any{"success": true, "marked": marked, "failed": failed, "cache_updated": cacheUpdated})
}

func (api *API) cachedMessages(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "system-messages", "default")
	if errors.Is(err, store.ErrMissing) {
		writeJSON(response, 200, map[string]any{"available": false, "messages": []any{}, "cache": map[string]any{}})
		return
	}
	if err != nil {
		api.resourceError(response, err)
		return
	}
	var payload academic.SystemMessages
	if err := json.Unmarshal(entry.Payload, &payload); err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"available": true, "messages": payload.Messages, "failed_kinds": payload.FailedKinds, "partial": len(payload.FailedKinds) > 0, "cache": entry.Metadata(messagesTTL)})
}
