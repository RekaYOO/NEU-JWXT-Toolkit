package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const jwxkQueryNamespace = "jwxk-catalog-queries"

func jwxkQueryKey(input academic.JWXKCatalogSearch) string {
	input.LocalOnly = false
	input.Keyword = strings.TrimSpace(input.Keyword)
	if len(input.Filters) == 0 {
		input.Filters = nil
	}
	encoded, _ := json.Marshal(input)
	digest := sha256.Sum256(encoded)
	return input.Batch + "-" + hex.EncodeToString(digest[:])
}

func (api *API) jwxkQuerySnapshot(ctx context.Context, session store.Session, key string) (store.Document, map[string]any, error) {
	generation, err := api.database.Generation(ctx, session.Account)
	if err != nil {
		return store.Document{}, nil, err
	}
	if generation != session.Generation {
		return store.Document{}, nil, store.ErrAccess
	}
	document, err := api.database.Read(ctx, session.Account, jwxkQueryNamespace, key)
	if errors.Is(err, store.ErrMissing) {
		return store.Document{}, nil, nil
	}
	if err != nil {
		return store.Document{}, nil, err
	}
	if document.Deleted {
		return document, nil, nil
	}
	var payload map[string]any
	if err := json.Unmarshal(document.Payload, &payload); err != nil {
		return document, nil, err
	}
	return document, payload, nil
}

func (api *API) saveJWXKQuerySnapshot(ctx context.Context, session store.Session, key string, payload map[string]any, revision int64) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = api.database.SaveForSession(ctx, session, jwxkQueryNamespace, key, encoded, revision, false)
	return err
}
