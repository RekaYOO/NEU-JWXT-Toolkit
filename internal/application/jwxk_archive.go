package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const jwxkArchiveNamespace = "jwxk-archives"

func jwxkArchiveID(batch string) string {
	digest := sha256.Sum256([]byte(batch))
	return hex.EncodeToString(digest[:16])
}

func jwxkArchiveRows(value any) []map[string]any {
	if rows, ok := value.([]map[string]any); ok {
		return rows
	}
	result := []map[string]any{}
	if rows, ok := value.([]any); ok {
		for _, row := range rows {
			if object := academic.Object(row); object != nil {
				result = append(result, object)
			}
		}
	}
	return result
}

func (api *API) readJWXKArchive(ctx context.Context, session store.Session, batch string) (store.Document, map[string]any, error) {
	generation, err := api.database.Generation(ctx, session.Account)
	if err != nil {
		return store.Document{}, nil, err
	}
	if generation != session.Generation {
		return store.Document{}, nil, store.ErrAccess
	}
	document, err := api.database.Read(ctx, session.Account, jwxkArchiveNamespace, jwxkArchiveID(batch))
	if errors.Is(err, store.ErrMissing) {
		return store.Document{}, nil, nil
	}
	if err != nil || document.Deleted {
		return document, nil, err
	}
	var payload map[string]any
	err = json.Unmarshal(document.Payload, &payload)
	return document, payload, err
}

func (api *API) mergeJWXKArchive(ctx context.Context, session store.Session, batch string, document store.Document, previous, payload map[string]any) error {
	if document.Deleted {
		return nil
	}
	if previous == nil {
		previous = map[string]any{"archive_id": jwxkArchiveID(batch), "batch_code": batch, "batch_name": batch, "created_at": time.Now().UTC().Format(time.RFC3339), "catalog_complete": false, "sync_status": "pending"}
	}
	incoming := []map[string]any{}
	for _, group := range jwxkArchiveRows(payload["groups"]) {
		incoming = append(incoming, jwxkArchiveRows(group["classes"])...)
	}
	previous["courses"] = academic.MergeJWXKArchive(jwxkArchiveRows(previous["courses"]), incoming)
	previous["scope_options"] = payload["scope_options"]
	previous["updated_at"] = time.Now().UTC().Format(time.RFC3339)
	round, err := api.readJWXKRound(ctx, session, batch)
	if err != nil {
		return err
	}
	for _, field := range []string{"term_code", "term_name", "selection_type_code", "selection_type", "begin_time", "end_time", "menus", "week_range"} {
		if round[field] != nil {
			previous[field] = round[field]
		}
	}
	if academic.Text(round["name"]) != "" {
		previous["batch_name"] = round["name"]
	}
	encoded, err := json.Marshal(previous)
	if err != nil {
		return err
	}
	_, err = api.database.SaveForSession(ctx, session, jwxkArchiveNamespace, jwxkArchiveID(batch), encoded, document.Revision, false)
	return err
}

func jwxkArchiveSupports(input academic.JWXKCatalogSearch) bool {
	if input.Order != "" || input.Scope == "ALLKC" {
		return false
	}
	for key, value := range input.Filters {
		if value == "" {
			continue
		}
		switch key {
		case "KCXZ", "KKDW", "KCLB", "XGXKLB", "XGXKLBDM", "SFCT", "SFYM", "SFYX", "SKXQ", "KSJC", "JSJC":
		default:
			return false
		}
	}
	return true
}

func (api *API) jwxkArchives(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	id := request.PathValue("archive_id")
	if id != "" {
		if decoded, err := hex.DecodeString(id); err != nil || len(decoded) != 16 {
			writeJSON(response, 422, map[string]string{"detail": "历史记录标识无效"})
			return
		}
	}
	documents, err := api.database.ListDocuments(request.Context(), session, jwxkArchiveNamespace)
	if err != nil {
		api.documentError(response, err)
		return
	}
	summaries := []map[string]any{}
	for _, document := range documents {
		if id != "" && document.Key != id {
			continue
		}
		var archive map[string]any
		if err := json.Unmarshal(document.Payload, &archive); err != nil {
			api.documentError(response, err)
			return
		}
		if id != "" {
			if request.Method == "DELETE" {
				if _, err := api.database.SaveForSession(request.Context(), session, jwxkArchiveNamespace, id, []byte("null"), document.Revision, true); err != nil {
					api.documentError(response, err)
					return
				}
				writeJSON(response, 200, map[string]bool{"success": true})
				return
			}
			writeJSON(response, 200, archive)
			return
		}
		count, selectable := 0, 0
		underfilled := []map[string]any{}
		for _, course := range jwxkArchiveRows(archive["courses"]) {
			if !academic.JWXKBusinessCourse(course) {
				continue
			}
			count++
			if course["eligibility_status"] == "selectable" {
				selectable++
			}
			participants := course["market_participant_count"]
			if participants == nil {
				participants = course["selected_count"]
				if archive["selection_type_code"] == "04" {
					participants = course["weight_participant_count"]
				}
			}
			if participants != nil && academic.Number(course["capacity"]) > academic.Number(participants) && course["eligibility_status"] != "unavailable" {
				underfilled = append(underfilled, course)
			}
		}
		archive["courses"], archive["course_count"], archive["selectable_count"] = underfilled, count, selectable
		summaries = append(summaries, archive)
	}
	if id != "" {
		writeJSON(response, 404, map[string]string{"detail": "历史课程备份不存在"})
		return
	}
	writeJSON(response, 200, map[string]any{"archives": summaries})
}
