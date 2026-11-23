package application

import (
	"context"
	"encoding/json"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) afterScoresRefresh(session store.Session, payload json.RawMessage, reason string) {
	if reason != "legacy_migration" {
		api.warmPolicyScores(session, payload)
	}
	if reason == "tracking" {
		return
	}
	api.warmScoreDetails(session, payload)
	api.resources.startDocumentWork(session, "grade-tracking-cache", "default", func(ctx context.Context, runtime *Runtime) error {
		if _, busy := api.trackingChecks.LoadOrStore(session.Account, true); busy {
			return nil
		}
		defer api.trackingChecks.Delete(session.Account)
		state, document, err := api.readTrackingState(ctx, session)
		if err != nil {
			return err
		}
		config, _, err := api.readGradeTrackingConfig(ctx, session)
		if err != nil || !config.Enabled {
			return err
		}
		entry, err := api.database.ReadCache(ctx, session, "scores", "default")
		if err != nil {
			return err
		}
		var scores academic.Scores
		if json.Unmarshal(entry.Payload, &scores) != nil {
			return store.ErrConflict
		}
		_, err = api.commitTrackingScores(ctx, session, config, state, document, scores)
		return err
	})
}
