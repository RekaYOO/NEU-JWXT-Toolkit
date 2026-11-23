package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const gradeTrackingNamespace = "grade-tracking"

func (api *API) readGradeTrackingConfig(ctx context.Context, session store.Session) (academic.GradeTrackingConfig, store.Document, error) {
	config := academic.DefaultGradeTrackingConfig()
	generation, err := api.database.Generation(ctx, session.Account)
	if err != nil {
		return config, store.Document{}, err
	}
	if generation != session.Generation {
		return config, store.Document{}, store.ErrAccess
	}
	document, err := api.database.Read(ctx, session.Account, gradeTrackingNamespace, "config")
	if errors.Is(err, store.ErrMissing) {
		return config, store.Document{}, nil
	}
	if err != nil || document.Deleted {
		return config, document, err
	}
	if json.Unmarshal(document.Payload, &config) != nil || !config.Valid() {
		return config, document, store.ErrConflict
	}
	if config.Enabled {
		state, _, err := api.readTrackingState(ctx, session)
		if err != nil {
			return config, document, err
		}
		if int64(academic.Number(state["generation"])) != session.Generation {
			config.Enabled = false
		}
	}
	return config, document, nil
}

func (api *API) gradeTrackingConfig(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	config, document, err := api.readGradeTrackingConfig(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if request.Method == "GET" {
		writeJSON(response, 200, config)
		return
	}
	var input struct {
		Interval *int `json:"interval_minutes"`
		Start    *int `json:"start_hour"`
		End      *int `json:"end_hour"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Interval != nil {
		config.IntervalMinutes = *input.Interval
	}
	if input.Start != nil {
		config.StartHour = *input.Start
	}
	if input.End != nil {
		config.EndHour = *input.End
	}
	if !config.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "检查间隔须为5–1440分钟，每日时段须为递增的整点范围"})
		return
	}
	payload, _ := json.Marshal(config)
	if _, err := api.database.SaveForSession(request.Context(), session, gradeTrackingNamespace, "config", payload, document.Revision, false); err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "config": config})
}
