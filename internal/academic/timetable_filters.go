package academic

import (
	"context"
	"encoding/json"
	"sort"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type TimetableFilterQuery struct {
	Mode    string         `json:"mode"`
	Term    string         `json:"term_code"`
	Keys    []string       `json:"keys"`
	Filters map[string]any `json:"filters"`
}

func (query TimetableFilterQuery) Valid() bool {
	if len(query.Keys) > 20 || !(TimetableTargetQuery{Mode: query.Mode, Term: query.Term, Page: 1, PageSize: 20, Filters: query.Filters}).Valid() {
		return false
	}
	for _, key := range query.Keys {
		if _, ok := targetFilters[query.Mode][key]; !ok {
			return false
		}
	}
	return true
}

func FetchTimetableFilters(ctx context.Context, requester Requester, query TimetableFilterQuery) (map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_TIMETABLE_QUERY", Message: "筛选目录条件无效"}
	}
	keys := query.Keys
	if len(keys) == 0 {
		keys = []string{}
		for key := range targetFilters[query.Mode] {
			keys = append(keys, key)
		}
	}
	options := map[string]map[string]string{}
	for _, key := range keys {
		if key != "has_schedule" && key != "min_capacity" && key != "max_capacity" {
			options[key] = map[string]string{}
		}
	}
	relations := map[string]map[string]string{}
	seen := map[string]bool{}
	totalPages, scanned := 1, 0
	for page := 1; page <= totalPages; page++ {
		payload, err := searchTimetableTargets(ctx, requester, TimetableTargetQuery{Mode: query.Mode, Term: query.Term, Page: page, PageSize: 1000, Filters: query.Filters})
		if err != nil {
			return nil, err
		}
		items := payload["items"].([]map[string]any)
		total := payload["total"].(int)
		pageSize := payload["page_size"].(int)
		if len(items) == 0 && (page > 1 || total > 0) {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表筛选目录提前返回空页"}
		}
		for _, item := range items {
			id := Text(item["id"])
			if seen[id] {
				return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课表筛选目录出现重复分页"}
			}
			seen[id] = true
			scanned++
			values := item["filter_values"].(map[string]string)
			details := item["details"].(map[string]string)
			relation := map[string]string{}
			for key, value := range values {
				if _, allowed := targetFilters[query.Mode][key]; allowed && value != "" {
					relation[key] = value
				}
				if choices, exists := options[key]; exists && value != "" {
					labelKey := key
					if key == "room_type" {
						labelKey = "type"
					}
					if _, exists := choices[value]; !exists {
						choices[value] = firstText(details[labelKey], value)
					}
				}
			}
			if len(relation) > 0 {
				encoded, _ := json.Marshal(relation)
				relations[string(encoded)] = relation
			}
		}
		pages := (total + pageSize - 1) / pageSize
		if pages > totalPages {
			totalPages = pages
		}
		if total > 20000 || scanned > 20000 || totalPages > 400 {
			return nil, &campus.Error{Code: "TIMETABLE_CATALOG_LIMIT", Message: "课表筛选目录超过安全上限，请缩小筛选范围"}
		}
	}
	resultOptions := map[string][]map[string]string{}
	for key, choices := range options {
		items := []map[string]string{}
		for value, label := range choices {
			items = append(items, map[string]string{"value": value, "label": label})
		}
		sort.Slice(items, func(left, right int) bool {
			if items[left]["label"] != items[right]["label"] {
				return items[left]["label"] < items[right]["label"]
			}
			return items[left]["value"] < items[right]["value"]
		})
		resultOptions[key] = items
	}
	relationKeys := []string{}
	for key := range relations {
		relationKeys = append(relationKeys, key)
	}
	sort.Strings(relationKeys)
	resultRelations := []map[string]string{}
	for _, key := range relationKeys {
		resultRelations = append(resultRelations, relations[key])
	}
	return map[string]any{"options": resultOptions, "relations": resultRelations}, nil
}
