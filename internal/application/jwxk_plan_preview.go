package application

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) jwxkPlanPreview(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Batch    string           `json:"batch_code"`
		Term     string           `json:"term_code"`
		Meetings []map[string]any `json:"meetings"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !(academic.JWXKSearch{Batch: input.Batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() || !(academic.TimetableContext{Mode: "personal", Term: input.Term}).Valid() || len(input.Meetings) > 200 {
		writeJSON(response, 422, map[string]string{"detail": "方案预览参数无效"})
		return
	}
	candidates := []academic.SelectionMeeting{}
	for _, row := range input.Meetings {
		if row == nil {
			writeJSON(response, 422, map[string]string{"detail": "课时不能为空"})
			return
		}
		candidates = append(candidates, academic.NormalizeSelectionMeeting(row, input.Term, "jwxk_plan"))
	}
	baseline := []academic.SelectionMeeting{}
	available, stale := false, true
	entry, err := api.database.ReadCache(request.Context(), session, "personal-timetable", "term:"+input.Term)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.resourceError(response, err)
		return
	}
	if err == nil {
		var payload struct {
			Term    string           `json:"term_code"`
			Courses []map[string]any `json:"courses"`
		}
		if json.Unmarshal(entry.Payload, &payload) == nil && payload.Term == input.Term && payload.Courses != nil && entry.SchemaVersion == 3 {
			available, stale = true, entry.Stale(timetableTTL)
			for _, row := range payload.Courses {
				if row != nil {
					baseline = append(baseline, academic.NormalizeSelectionMeeting(row, input.Term, "personal_timetable"))
				}
			}
		}
	}
	writeJSON(response, 200, academic.PreviewSelectionConflicts(input.Term, baseline, candidates, available, stale))
}
