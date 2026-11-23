package application

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const jwxkRoundNamespace = "jwxk-rounds"

func (api *API) saveJWXKRounds(ctx context.Context, session store.Session, payload map[string]any) error {
	rows, _ := payload["batches"].([]map[string]any)
	for _, row := range rows {
		code := academic.Text(row["code"])
		if !(academic.JWXKSearch{Batch: code, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
			return store.ErrConflict
		}
		snapshot := map[string]any{"code": code, "state": "unknown", "can_enter": false, "snapshot": true}
		for _, field := range []string{"name", "term_code", "term_name", "selection_type_code", "selection_type", "begin_time", "end_time", "menus", "course_types", "week_range"} {
			snapshot[field] = row[field]
		}
		previous, err := api.database.Read(ctx, session.Account, jwxkRoundNamespace, code)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return err
		}
		if previous.Deleted {
			continue
		}
		encoded, err := json.Marshal(snapshot)
		if err != nil {
			return err
		}
		if _, err := api.database.SaveForSession(ctx, session, jwxkRoundNamespace, code, encoded, previous.Revision, false); err != nil {
			return err
		}
	}
	return nil
}

func (api *API) readJWXKRound(ctx context.Context, session store.Session, batch string) (map[string]any, error) {
	documents, err := api.database.ListDocuments(ctx, session, jwxkRoundNamespace)
	if err != nil {
		return nil, err
	}
	for _, document := range documents {
		if document.Key != batch {
			continue
		}
		var snapshot map[string]any
		if json.Unmarshal(document.Payload, &snapshot) != nil || academic.Text(snapshot["code"]) != batch {
			return nil, store.ErrConflict
		}
		snapshot["state"], snapshot["can_enter"], snapshot["snapshot"] = "unknown", false, true
		return snapshot, nil
	}
	return nil, nil
}
