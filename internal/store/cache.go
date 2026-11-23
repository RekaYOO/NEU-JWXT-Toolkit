package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

type CacheEntry struct {
	Resource          string
	Variant           string
	SchemaVersion     int
	Payload           json.RawMessage
	Revision          string
	SavedAt           string
	CheckedAt         string
	AttemptedAt       string
	ErrorKind         string
	Invalidated       bool
	Dependencies      map[string]string
	Changes           map[string]any
	PreserveFreshness bool
	Policy            *CachePolicy
}

func (instance *Store) DeleteCacheForSession(ctx context.Context, session Session, resource, variant string) (bool, error) {
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer transaction.Rollback()
	var generation int64
	var enabled bool
	err = transaction.QueryRowContext(ctx, "SELECT generation,enabled FROM accounts WHERE id=?", session.Account).Scan(&generation, &enabled)
	if errors.Is(err, sql.ErrNoRows) || err == nil && (!enabled || generation != session.Generation) {
		return false, ErrAccess
	}
	if err != nil {
		return false, err
	}
	result, err := transaction.ExecContext(ctx, "DELETE FROM cache_entries WHERE account_id=? AND resource=? AND variant=?", session.Account, resource, variant)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return false, err
	}
	return count > 0, transaction.Commit()
}

func (entry CacheEntry) Stale(ttl time.Duration) bool {
	if entry.Policy != nil {
		if !entry.Policy.Enabled {
			return true
		}
		ttl = time.Duration(entry.Policy.IntervalMinutes) * time.Minute
	}
	checked, err := time.Parse(time.RFC3339Nano, entry.CheckedAt)
	return entry.Invalidated || err != nil || time.Since(checked) > ttl
}

func (entry CacheEntry) Metadata(ttl time.Duration) map[string]any {
	return map[string]any{"schema_version": entry.SchemaVersion, "revision_algorithm_version": 1,
		"revision": entry.Revision, "saved_at": entry.SavedAt, "last_checked_at": entry.CheckedAt,
		"last_attempt_at": entry.AttemptedAt, "last_error_kind": entry.ErrorKind,
		"dependency_revisions": entry.Dependencies, "is_stale": entry.Stale(ttl)}
}

type CacheEvent struct {
	Cursor           int64          `json:"cursor"`
	Resource         string         `json:"resource"`
	Variant          string         `json:"variant"`
	PreviousRevision string         `json:"previous_revision"`
	Revision         string         `json:"revision"`
	Changed          bool           `json:"changed"`
	Reason           string         `json:"reason"`
	CreatedAt        string         `json:"created_at"`
	Changes          map[string]any `json:"changes"`
}

func (instance *Store) InvalidateCache(ctx context.Context, session Session, resource, variant string) error {
	_, err := instance.database.ExecContext(ctx, `UPDATE cache_entries SET invalidated=1 WHERE account_id=? AND resource=? AND variant=?
	 AND EXISTS(SELECT 1 FROM accounts WHERE id=? AND enabled=1 AND generation=?)`, session.Account, resource, variant, session.Account, session.Generation)
	return err
}

func (instance *Store) LatestCachedAccount(ctx context.Context) (Session, error) {
	var session Session
	err := instance.database.QueryRowContext(ctx, `SELECT a.id,a.generation FROM accounts a JOIN cache_entries c ON c.account_id=a.id
	 WHERE a.enabled=1 AND c.resource IN ('scores','academic-report','research-training','experiment-courses','festival-activities','system-messages')
	 ORDER BY c.checked_at DESC,a.id LIMIT 1`).Scan(&session.Account, &session.Generation)
	if errors.Is(err, sql.ErrNoRows) {
		return Session{}, ErrMissing
	}
	return session, err
}

func (instance *Store) CacheVariants(ctx context.Context, session Session, resource string) ([]string, error) {
	rows, err := instance.database.QueryContext(ctx, `SELECT c.variant FROM cache_entries c JOIN accounts a ON a.id=c.account_id WHERE a.id=? AND a.enabled=1 AND a.generation=? AND c.resource=? ORDER BY c.variant`, session.Account, session.Generation, resource)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	variants := []string{}
	for rows.Next() {
		var variant string
		if err := rows.Scan(&variant); err != nil {
			return nil, err
		}
		variants = append(variants, variant)
	}
	return variants, rows.Err()
}

func (instance *Store) ReadCache(ctx context.Context, session Session, resource, variant string) (CacheEntry, error) {
	entry := CacheEntry{Resource: resource, Variant: variant}
	var dependencies []byte
	err := instance.database.QueryRowContext(ctx, `SELECT c.schema_version,c.payload,c.revision,c.saved_at,c.checked_at,c.attempted_at,c.error_kind,c.invalidated,c.dependencies
	 FROM cache_entries c JOIN accounts a ON a.id=c.account_id
	 WHERE a.id=? AND a.enabled=1 AND a.generation=? AND c.resource=? AND c.variant=?`,
		session.Account, session.Generation, resource, variant).Scan(&entry.SchemaVersion, &entry.Payload, &entry.Revision, &entry.SavedAt, &entry.CheckedAt, &entry.AttemptedAt, &entry.ErrorKind, &entry.Invalidated, &dependencies)
	if errors.Is(err, sql.ErrNoRows) {
		return CacheEntry{}, ErrMissing
	}
	if err != nil {
		return CacheEntry{}, err
	}
	if err := json.Unmarshal(dependencies, &entry.Dependencies); err != nil {
		return CacheEntry{}, err
	}
	policies, _, err := instance.CachePolicies(ctx, session.Account)
	if err != nil {
		return CacheEntry{}, err
	}
	if policy, exists := policies[resource]; exists {
		entry.Policy = &policy
	}
	return entry, nil
}

func (instance *Store) CommitCache(ctx context.Context, session Session, entry CacheEntry, reason string, invalidations []string) (bool, error) {
	if !json.Valid(entry.Payload) || entry.Revision == "" || entry.Resource == "" || entry.Variant == "" {
		return false, errors.New("invalid cache entry")
	}
	dependencies, err := json.Marshal(entry.Dependencies)
	if err != nil {
		return false, err
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer transaction.Rollback()
	var generation int64
	if err := transaction.QueryRowContext(ctx, "SELECT generation FROM accounts WHERE id=? AND enabled=1", session.Account).Scan(&generation); err != nil || generation != session.Generation {
		return false, ErrAccess
	}
	previous := ""
	err = transaction.QueryRowContext(ctx, "SELECT revision FROM cache_entries WHERE account_id=? AND resource=? AND variant=?", session.Account, entry.Resource, entry.Variant).Scan(&previous)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return false, err
	}
	changed := previous != entry.Revision
	changes := entry.Changes
	if changes == nil {
		changes = map[string]any{"initial": previous == ""}
	}
	encodedChanges, err := json.Marshal(changes)
	if err != nil {
		return false, err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	checkedAt, attemptedAt := now, now
	if entry.PreserveFreshness {
		checkedAt, attemptedAt = entry.CheckedAt, entry.AttemptedAt
	}
	_, err = transaction.ExecContext(ctx, `INSERT INTO cache_entries(account_id,resource,variant,schema_version,payload,revision,saved_at,checked_at,attempted_at,error_kind,invalidated,dependencies)
	 VALUES(?,?,?,?,?,?,?,?,?,'',0,?) ON CONFLICT(account_id,resource,variant) DO UPDATE SET
	 schema_version=excluded.schema_version,payload=excluded.payload,revision=excluded.revision,
	 saved_at=CASE WHEN cache_entries.revision=excluded.revision THEN cache_entries.saved_at ELSE excluded.saved_at END,
	 checked_at=excluded.checked_at,attempted_at=excluded.attempted_at,error_kind='',invalidated=0,dependencies=excluded.dependencies`,
		session.Account, entry.Resource, entry.Variant, entry.SchemaVersion, []byte(entry.Payload), entry.Revision, now, checkedAt, attemptedAt, dependencies)
	if err != nil {
		return false, err
	}
	if changed {
		for _, resource := range invalidations {
			if _, err := transaction.ExecContext(ctx, "UPDATE cache_entries SET invalidated=1 WHERE account_id=? AND resource=?", session.Account, resource); err != nil {
				return false, err
			}
		}
	}
	if _, err := transaction.ExecContext(ctx, `INSERT INTO cache_events(account_id,resource,variant,previous_revision,revision,changed,reason,created_at,changes) VALUES(?,?,?,?,?,?,?,?,?)`,
		session.Account, entry.Resource, entry.Variant, previous, entry.Revision, changed, reason, now, encodedChanges); err != nil {
		return false, err
	}
	return changed, transaction.Commit()
}

func (instance *Store) CacheFailure(ctx context.Context, session Session, resource, variant, kind string) error {
	_, err := instance.database.ExecContext(ctx, `UPDATE cache_entries SET error_kind=?,attempted_at=?
	 WHERE account_id=? AND resource=? AND variant=? AND EXISTS(SELECT 1 FROM accounts WHERE id=? AND enabled=1 AND generation=?)`,
		kind, time.Now().UTC().Format(time.RFC3339Nano), session.Account, resource, variant, session.Account, session.Generation)
	return err
}

func (instance *Store) CacheEvents(ctx context.Context, session Session, after *int64, limit int) ([]CacheEvent, int64, error) {
	events := []CacheEvent{}
	if after == nil {
		var cursor int64
		err := instance.database.QueryRowContext(ctx, "SELECT COALESCE(MAX(cursor),0) FROM cache_events WHERE account_id=?", session.Account).Scan(&cursor)
		return events, cursor, err
	}
	if limit < 1 || limit > 200 {
		limit = 100
	}
	rows, err := instance.database.QueryContext(ctx, `SELECT cursor,resource,variant,previous_revision,revision,changed,reason,created_at,changes FROM cache_events WHERE account_id=? AND cursor>? ORDER BY cursor LIMIT ?`, session.Account, *after, limit)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	cursor := *after
	for rows.Next() {
		var event CacheEvent
		var changes []byte
		if err := rows.Scan(&event.Cursor, &event.Resource, &event.Variant, &event.PreviousRevision, &event.Revision, &event.Changed, &event.Reason, &event.CreatedAt, &changes); err != nil {
			return nil, 0, err
		}
		if err := json.Unmarshal(changes, &event.Changes); err != nil {
			return nil, 0, err
		}
		events = append(events, event)
		cursor = event.Cursor
	}
	return events, cursor, rows.Err()
}
