package store

import (
	"context"
	"encoding/json"
	"errors"
)

const MaxCacheIntervalMinutes = 52560000

type CachePolicy struct {
	Enabled         bool  `json:"enabled"`
	IntervalMinutes int64 `json:"interval_minutes"`
}

func (instance *Store) CachePolicies(ctx context.Context, account string) (map[string]CachePolicy, int64, error) {
	document, err := instance.Read(ctx, account, "settings", "cache")
	policies := map[string]CachePolicy{}
	if errors.Is(err, ErrMissing) {
		return policies, 0, nil
	}
	if err != nil {
		return nil, 0, err
	}
	if document.Deleted {
		return policies, document.Revision, nil
	}
	if err := json.Unmarshal(document.Payload, &policies); err != nil {
		return nil, 0, err
	}
	for _, policy := range policies {
		if policy.IntervalMinutes < 1 || policy.IntervalMinutes > MaxCacheIntervalMinutes {
			return nil, 0, errors.New("invalid cache policy")
		}
	}
	return policies, document.Revision, nil
}
