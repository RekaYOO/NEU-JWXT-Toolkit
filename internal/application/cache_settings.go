package application

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) cacheSettings(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	current, revision, err := api.database.CachePolicies(request.Context(), session.Account)
	if err != nil {
		api.documentError(response, err)
		return
	}
	settings := map[string]store.CachePolicy{}
	for resource, spec := range api.resources.specs {
		settings[resource] = store.CachePolicy{Enabled: true, IntervalMinutes: max(1, min(store.MaxCacheIntervalMinutes, int64(spec.TTL/time.Minute)))}
		if saved, exists := current[resource]; exists {
			settings[resource] = saved
		}
	}
	if request.Method == http.MethodPut {
		var input struct {
			Resources map[string]*struct {
				Enabled         *bool  `json:"enabled"`
				IntervalMinutes *int64 `json:"interval_minutes"`
			} `json:"resources"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		for resource, update := range input.Resources {
			if _, exists := settings[resource]; !exists {
				writeJSON(response, 400, map[string]string{"detail": "未知缓存资源"})
				return
			}
			if update == nil || update.Enabled == nil || update.IntervalMinutes == nil || *update.IntervalMinutes < 1 || *update.IntervalMinutes > store.MaxCacheIntervalMinutes {
				writeJSON(response, 422, map[string]string{"detail": "请提供缓存状态及有效的整数检查间隔"})
				return
			}
			settings[resource] = store.CachePolicy{Enabled: *update.Enabled, IntervalMinutes: *update.IntervalMinutes}
		}
		payload, _ := json.Marshal(settings)
		if _, err := api.database.SaveForSession(request.Context(), session, "settings", "cache", payload, revision, false); err != nil {
			api.documentError(response, err)
			return
		}
	}
	writeJSON(response, 200, map[string]any{"cache": settings})
}
