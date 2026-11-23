package application

import (
	"encoding/json"
	"net/http"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

type filterCatalogEntry struct {
	saved   time.Time
	payload map[string]any
}
type filterCatalogState struct {
	mutex   sync.Mutex
	entries map[string]filterCatalogEntry
}

func (api *API) timetableFilters(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var input academic.TimetableFilterQuery
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "筛选目录条件无效"})
		return
	}
	sort.Strings(input.Keys)
	encoded, _ := json.Marshal(input)
	key := string(encoded)
	value, _ := api.filterCatalogs.LoadOrStore(session.Account+":"+strconv.FormatInt(session.Generation, 10), &filterCatalogState{entries: map[string]filterCatalogEntry{}})
	state := value.(*filterCatalogState)
	state.mutex.Lock()
	defer state.mutex.Unlock()
	for existing, entry := range state.entries {
		if time.Since(entry.saved) >= 30*time.Minute {
			delete(state.entries, existing)
		}
	}
	if entry, exists := state.entries[key]; exists {
		writeJSON(response, 200, entry.payload)
		return
	}
	if request.Context().Err() != nil {
		return
	}
	payload, err := academic.FetchTimetableFilters(request.Context(), runtime, input)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if len(state.entries) >= 12 {
		oldest := ""
		var saved time.Time
		for existing, entry := range state.entries {
			if oldest == "" || entry.saved.Before(saved) {
				oldest, saved = existing, entry.saved
			}
		}
		delete(state.entries, oldest)
	}
	state.entries[key] = filterCatalogEntry{saved: time.Now(), payload: payload}
	writeJSON(response, 200, payload)
}
