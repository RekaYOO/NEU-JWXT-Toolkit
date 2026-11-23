package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const researchTTL = 5 * time.Minute

type researchFavorite struct {
	Batch     string         `json:"batch_id"`
	BatchName string         `json:"batch_name"`
	Topic     string         `json:"topic_id"`
	Snapshot  map[string]any `json:"topic"`
	Created   string         `json:"favorited_at"`
}

func researchFavoriteKey(batch, topic string) string {
	encoded, _ := json.Marshal([]string{batch, topic})
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func researchRowsByID(payload map[string]any, field, identifier string) map[string]map[string]any {
	encoded, _ := json.Marshal(payload[field])
	var rows []map[string]any
	json.Unmarshal(encoded, &rows)
	result := map[string]map[string]any{}
	for _, row := range rows {
		result[academic.Text(row[identifier])] = row
	}
	return result
}

func researchSemantic(payload map[string]any) json.RawMessage {
	result := map[string]any{"batch": payload["batch"], "eligibility": payload["eligibility"]}
	for field, identifier := range map[string]string{"topics": "topic_id", "confirmed_topics": "record_id"} {
		rows := researchRowsByID(payload, field, identifier)
		keys := make([]string, 0, len(rows))
		for key := range rows {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		ordered := make([]map[string]any, 0, len(keys))
		for _, key := range keys {
			ordered = append(ordered, rows[key])
		}
		result[field] = ordered
	}
	encoded, _ := json.Marshal(result)
	return encoded
}

func researchDiff(before, after json.RawMessage) map[string]any {
	var previous, current map[string]any
	if json.Unmarshal(before, &previous) != nil || json.Unmarshal(after, &current) != nil {
		return map[string]any{}
	}
	added, changed, removed := []string{}, []string{}, []string{}
	oldRows, newRows := researchRowsByID(previous, "topics", "topic_id"), researchRowsByID(current, "topics", "topic_id")
	batchChanged := academic.Text(academic.Object(previous["batch"])["batch_id"]) != academic.Text(academic.Object(current["batch"])["batch_id"])
	if !batchChanged {
		for key, row := range newRows {
			old, exists := oldRows[key]
			if !exists {
				added = append(added, key)
				continue
			}
			oldEncoded, _ := json.Marshal(old)
			newEncoded, _ := json.Marshal(row)
			if string(oldEncoded) != string(newEncoded) {
				changed = append(changed, key)
			}
		}
		for key := range oldRows {
			if newRows[key] == nil {
				removed = append(removed, key)
			}
		}
	}
	sort.Strings(added)
	sort.Strings(changed)
	sort.Strings(removed)
	oldConfirmed, _ := json.Marshal(researchRowsByID(previous, "confirmed_topics", "record_id"))
	newConfirmed, _ := json.Marshal(researchRowsByID(current, "confirmed_topics", "record_id"))
	return map[string]any{"added_topic_ids": added, "changed_topic_ids": changed, "removed_topic_ids": removed, "batch_changed": batchChanged, "confirmed_changed": string(oldConfirmed) != string(newConfirmed)}
}

func (api *API) registerResearchResource() {
	api.resources.Register("research-training", ResourceSpec{TTL: researchTTL, Offline: true, PublicRefresh: true, Fetch: func(ctx context.Context, runtime *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		payload, err := academic.FetchResearchSnapshot(ctx, runtime)
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(payload)
		return encoded, researchSemantic(payload), err
	}, Diff: researchDiff, After: api.syncResearchFavorites})
}

func (api *API) registerResearch(router *http.ServeMux) {
	router.HandleFunc("POST /api/research-training/enroll", api.researchMutation)
	router.HandleFunc("POST /api/research-training/cancel", api.researchMutation)
	router.HandleFunc("POST /api/research-training/verify", api.researchMutation)
	router.HandleFunc("GET /api/research-training", api.researchList)
	router.HandleFunc("GET /api/research-training/confirmed", api.researchList)
	router.HandleFunc("POST /api/research-training/refresh", api.refreshResearch)
	router.HandleFunc("GET /api/research-training/cache", api.cachedResearch)
	router.HandleFunc("GET /api/offline/research-training", api.offlineHandler(api.cachedResearch))
	router.HandleFunc("POST /api/research-training/favorite", api.setResearchFavorite)
	router.HandleFunc("GET /api/research-training/topics/{topic_id}", api.researchDetail)
	router.HandleFunc("GET /api/research-training/confirmed/{record_id}/journals", api.researchDetail)
}

func (api *API) waitResearchRefresh(ctx context.Context, session store.Session, force bool) error {
	result, err := api.resources.Submit(ctx, session, "research-training", "default", "manual", force)
	if err != nil {
		return err
	}
	identifier, _ := result["job_id"].(string)
	if identifier == "" {
		if result["status"] == "fresh" {
			return nil
		}
		return errors.New("科研训练暂时无法刷新，请稍后重试")
	}
	job, exists := api.resources.Job(session, identifier)
	if !exists {
		return store.ErrMissing
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-job.done:
	}
	job, exists = api.resources.Job(session, identifier)
	if !exists || job.Status != "completed" {
		return errors.New("科研训练刷新失败，已保留旧缓存")
	}
	return nil
}

func (api *API) refreshResearch(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	previous, _ := api.database.ReadCache(request.Context(), session, "research-training", "default")
	if err := api.waitResearchRefresh(request.Context(), session, true); err != nil {
		api.resourceError(response, err)
		return
	}
	payload, err := api.researchCache(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	current, err := api.database.ReadCache(request.Context(), session, "research-training", "default")
	if err != nil {
		api.resourceError(response, err)
		return
	}
	diff := researchDiff(previous.Payload, current.Payload)
	count := func(key string) int { rows, _ := diff[key].([]string); return len(rows) }
	payload["update_available"] = previous.Revision != "" && previous.Revision != current.Revision
	payload["changes"] = map[string]any{"added": count("added_topic_ids"), "updated": count("changed_topic_ids"), "removed": count("removed_topic_ids"), "new_batch": diff["batch_changed"] == true, "confirmed_changed": diff["confirmed_changed"] == true}
	writeJSON(response, 200, payload)
}

func (api *API) researchList(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	page, size := 1, 20
	for key, target := range map[string]*int{"page": &page, "page_size": &size} {
		if value := request.URL.Query().Get(key); value != "" {
			number, err := strconv.Atoi(value)
			if err != nil || number < 1 || number > 10000 || (key == "page_size" && number > 50) {
				writeJSON(response, 422, map[string]string{"detail": "分页参数无效"})
				return
			}
			*target = number
		}
	}
	for key, limit := range map[string]int{"keyword": 100, "project_name": 100, "advisor_name": 50} {
		if len([]rune(request.URL.Query().Get(key))) > limit {
			writeJSON(response, 422, map[string]string{"detail": "搜索参数过长"})
			return
		}
	}
	if err := api.waitResearchRefresh(request.Context(), session, false); err != nil {
		api.resourceError(response, err)
		return
	}
	payload, err := api.researchCache(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if strings.HasSuffix(request.URL.Path, "/confirmed") {
		payload["topics"] = payload["confirmed_topics"]
		payload["total"] = len(researchRowsByID(payload, "confirmed_topics", "record_id"))
	} else {
		encoded, _ := json.Marshal(payload["topics"])
		var rows []map[string]any
		json.Unmarshal(encoded, &rows)
		filtered := []map[string]any{}
		for _, row := range rows {
			matches := true
			for query, field := range map[string]string{"keyword": "title", "project_name": "project_name", "advisor_name": "advisor_name"} {
				if !strings.Contains(strings.ToLower(academic.Text(row[field])), strings.ToLower(strings.TrimSpace(request.URL.Query().Get(query)))) {
					matches = false
				}
			}
			if matches {
				filtered = append(filtered, row)
			}
		}
		start := min((page-1)*size, len(filtered))
		end := min(start+size, len(filtered))
		payload["topics"], payload["total"], payload["page"], payload["page_size"] = filtered[start:end], len(filtered), page, size
	}
	writeJSON(response, 200, payload)
}

func (api *API) researchCache(ctx context.Context, session store.Session) (map[string]any, error) {
	entry, err := api.database.ReadCache(ctx, session, "research-training", "default")
	payload := map[string]any{"available": false, "batch": map[string]any{}, "eligibility": map[string]any{}, "topics": []any{}, "confirmed_topics": []any{}, "total": 0}
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return nil, err
	}
	if err == nil {
		if err := json.Unmarshal(entry.Payload, &payload); err != nil {
			return nil, err
		}
		payload["available"], payload["cache"], payload["revision"], payload["saved_at"] = true, entry.Metadata(researchTTL), entry.Revision, entry.SavedAt
	}
	payload["username"] = session.Account
	payload["version"], payload["update_available"] = 1, false
	payload["changes"] = map[string]any{"added": 0, "updated": 0, "removed": 0, "new_batch": false, "confirmed_changed": false}
	if err := api.decorateResearchFavorites(ctx, session, payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func (api *API) cachedResearch(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	payload, err := api.researchCache(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if _, offline := request.Context().Value(offlineIdentityKey{}).(store.Session); offline {
		payload["source"], payload["is_fresh"] = "offline", false
	}
	writeJSON(response, 200, payload)
}

func (api *API) decorateResearchFavorites(ctx context.Context, session store.Session, payload map[string]any) error {
	documents, err := api.database.ListDocuments(ctx, session, "research-favorites")
	if err != nil {
		return err
	}
	currentBatch := academic.Text(academic.Object(payload["batch"])["batch_id"])
	current := researchRowsByID(payload, "topics", "topic_id")
	identifiers, favorites := []string{}, []map[string]any{}
	for _, document := range documents {
		var favorite researchFavorite
		if json.Unmarshal(document.Payload, &favorite) != nil || favorite.Snapshot == nil {
			return store.ErrConflict
		}
		row := favorite.Snapshot
		active := favorite.Batch == currentBatch && current[favorite.Topic] != nil
		if favorite.Batch == currentBatch {
			identifiers = append(identifiers, favorite.Topic)
		}
		if active {
			row = current[favorite.Topic]
		}
		item := map[string]any{}
		for key, value := range row {
			item[key] = value
		}
		item["favorite_batch_id"], item["favorite_batch_name"], item["favorited_at"], item["expired"] = favorite.Batch, favorite.BatchName, favorite.Created, !active
		if !active {
			item["can_enroll"], item["can_cancel"] = false, false
		}
		favorites = append(favorites, item)
	}
	sort.Strings(identifiers)
	payload["favorite_topic_ids"], payload["favorite_topics"] = identifiers, favorites
	return nil
}

func (api *API) setResearchFavorite(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Batch    string `json:"batch_id"`
		Topic    string `json:"topic_id"`
		Favorite *bool  `json:"favorite"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !academic.ValidEvaluationID(input.Batch) || !academic.ValidEvaluationID(input.Topic) || input.Favorite == nil {
		writeJSON(response, 422, map[string]string{"detail": "收藏参数无效"})
		return
	}
	release, err := api.resources.acquireAccount(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	payload, err := api.researchCache(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	batch := academic.Object(payload["batch"])
	topic := researchRowsByID(payload, "topics", "topic_id")[input.Topic]
	if *input.Favorite && (academic.Text(batch["batch_id"]) != input.Batch || topic == nil) {
		writeJSON(response, 409, map[string]string{"detail": "课题或批次已变化，请刷新后重试"})
		return
	}
	key := researchFavoriteKey(input.Batch, input.Topic)
	previous, err := api.database.Read(request.Context(), session.Account, "research-favorites", key)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.resourceError(response, err)
		return
	}
	favorite := researchFavorite{Batch: input.Batch, BatchName: academic.Text(batch["name"]), Topic: input.Topic, Snapshot: topic, Created: time.Now().UTC().Format(time.RFC3339Nano)}
	encoded, _ := json.Marshal(favorite)
	if !*input.Favorite {
		encoded = json.RawMessage(`null`)
	}
	if _, err := api.database.SaveForSession(request.Context(), session, "research-favorites", key, encoded, previous.Revision, !*input.Favorite); err != nil {
		api.resourceError(response, err)
		return
	}
	if err := api.decorateResearchFavorites(request.Context(), session, payload); err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "favorite": *input.Favorite, "favorite_topic_ids": payload["favorite_topic_ids"], "favorite_topics": payload["favorite_topics"]})
}

func (api *API) syncResearchFavorites(session store.Session, encoded json.RawMessage) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	release, err := api.resources.acquireAccount(ctx, session)
	if err != nil {
		return
	}
	defer release()
	entry, err := api.database.ReadCache(ctx, session, "research-training", "default")
	if err != nil {
		return
	}
	encoded = entry.Payload
	var payload map[string]any
	if json.Unmarshal(encoded, &payload) != nil {
		return
	}
	batch := academic.Object(payload["batch"])
	topics := researchRowsByID(payload, "topics", "topic_id")
	documents, err := api.database.ListDocuments(ctx, session, "research-favorites")
	if err != nil {
		return
	}
	for _, document := range documents {
		var favorite researchFavorite
		if json.Unmarshal(document.Payload, &favorite) != nil || favorite.Batch != academic.Text(batch["batch_id"]) || topics[favorite.Topic] == nil {
			continue
		}
		favorite.Snapshot, favorite.BatchName = topics[favorite.Topic], academic.Text(batch["name"])
		updated, _ := json.Marshal(favorite)
		api.database.SaveForSession(ctx, session, "research-favorites", document.Key, updated, document.Revision, false)
	}
}

func (api *API) researchDetail(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	identifier := request.PathValue("topic_id")
	journal := request.PathValue("record_id") != ""
	if journal {
		identifier = request.PathValue("record_id")
	}
	if !academic.ValidEvaluationID(identifier) {
		writeJSON(response, 422, map[string]string{"detail": "科研课题或记录编号无效"})
		return
	}
	var payload any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		if journal {
			var rows []map[string]any
			rows, err = academic.FetchResearchJournals(request.Context(), runtime, identifier)
			payload = map[string]any{"record_id": identifier, "journals": rows}
		} else {
			payload, err = academic.FetchResearchTopicDetail(request.Context(), runtime, identifier)
		}
		return err
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}
