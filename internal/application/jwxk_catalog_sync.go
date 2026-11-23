package application

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) updateJWXKQueryCourses(ctx context.Context, session store.Session, batch string, update func(map[string]any)) error {
	documents, err := api.database.ListDocuments(ctx, session, jwxkQueryNamespace)
	if err != nil {
		return err
	}
	archives, err := api.database.ListDocuments(ctx, session, jwxkArchiveNamespace)
	if err != nil {
		return err
	}
	documents = append(documents, archives...)
	for _, document := range documents {
		isArchive := document.Key == jwxkArchiveID(batch)
		if !isArchive && (len(document.Key) != len(batch)+65 || !strings.HasPrefix(document.Key, batch+"-")) {
			continue
		}
		var payload map[string]any
		if err := json.Unmarshal(document.Payload, &payload); err != nil {
			return err
		}
		groups, ok := payload["groups"].([]any)
		if isArchive {
			groups, ok = []any{map[string]any{"classes": payload["courses"]}}, true
		}
		if !ok {
			return store.ErrConflict
		}
		for _, rawGroup := range groups {
			group := academic.Object(rawGroup)
			if group == nil {
				return store.ErrConflict
			}
			classes, ok := group["classes"].([]any)
			if !ok {
				return store.ErrConflict
			}
			selectable, pending := 0, 0
			for _, rawClass := range classes {
				course := academic.Object(rawClass)
				if course == nil {
					return store.ErrConflict
				}
				update(course)
				if course["eligibility_status"] == "selectable" && course["restricted"] != true && course["full"] != true {
					selectable++
				}
				if course["eligibility_status"] == "unknown" {
					pending++
				}
			}
			group["selectable_count"], group["eligibility_pending_count"] = selectable, pending
		}
		encoded, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		var previous, updated any
		json.Unmarshal(document.Payload, &previous)
		json.Unmarshal(encoded, &updated)
		if reflect.DeepEqual(previous, updated) {
			continue
		}
		namespace := jwxkQueryNamespace
		if isArchive {
			namespace = jwxkArchiveNamespace
		}
		if _, err := api.database.SaveForSession(ctx, session, namespace, document.Key, encoded, document.Revision, false); err != nil {
			return err
		}
	}
	return nil
}

func (api *API) syncJWXKSelectedSnapshot(ctx context.Context, session store.Session, batch string, official map[string]any) error {
	if official["complete"] != true {
		return nil
	}
	mode := academic.Text(official["selection_type_code"])
	if mode != "02" && mode != "04" {
		return nil
	}
	feed := "selected"
	if mode == "04" {
		feed = "volunteered"
	}
	classes, codes := map[string]bool{}, map[string]bool{}
	weights := map[string]any{}
	for _, course := range official[feed].([]map[string]any) {
		if course["current_batch_record"] != true {
			continue
		}
		class, code := academic.Text(course["class_id"]), strings.ToUpper(academic.Text(course["course_code"]))
		if class != "" {
			classes[class] = true
			weights[class] = course["devoted_weight"]
		}
		if code != "" {
			codes[code] = true
		}
	}
	return api.updateJWXKQueryCourses(ctx, session, batch, func(course map[string]any) {
		class := academic.Text(course["class_id"])
		course["selected"] = classes[class]
		course["course_already_selected"] = codes[strings.ToUpper(academic.Text(course["course_code"]))]
		course["devoted_weight"] = weights[class]
	})
}

func (api *API) syncJWXKEligibilitySnapshot(ctx context.Context, session store.Session, batch string, results map[string]any) error {
	byClass := map[string]map[string]any{}
	for _, result := range results["results"].([]map[string]any) {
		byClass[academic.Text(result["class_id"])] = result
	}
	return api.updateJWXKQueryCourses(ctx, session, batch, func(course map[string]any) {
		if result := byClass[academic.Text(course["class_id"])]; result != nil {
			course["eligibility_status"], course["eligibility_reason"] = result["status"], result["reason"]
		}
	})
}
