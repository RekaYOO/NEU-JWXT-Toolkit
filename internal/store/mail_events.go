package store

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strconv"
	"time"
)

func (instance *Store) ObserveMailEvent(ctx context.Context, session Session, source, scope, signature string, payload json.RawMessage) (bool, error) {
	return instance.observeMailEvent(ctx, session, source, scope, signature, payload, true)
}

func (instance *Store) ObserveMailTransition(ctx context.Context, session Session, source, scope, signature string, payload json.RawMessage) (bool, error) {
	return instance.observeMailEvent(ctx, session, source, scope, signature, payload, false)
}

func (instance *Store) observeMailEvent(ctx context.Context, session Session, source, scope, signature string, payload json.RawMessage, notifyInitial bool) (bool, error) {
	if source == "" || len(source) > 64 || scope == "" || len(scope) > 400 || signature == "" || len(signature) > 128 || len(payload) > 1024*1024 {
		return false, errors.New("invalid notification event")
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
	var previous string
	var revision int64
	err = transaction.QueryRowContext(ctx, `SELECT signature,revision FROM mail_events WHERE account_id=? AND generation=? AND source=? AND scope=?`, session.Account, session.Generation, source, scope).Scan(&previous, &revision)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return false, err
	}
	if previous == signature {
		return false, nil
	}
	if revision == 0 && !notifyInitial {
		payload = nil
	}
	revision++
	if _, err := transaction.ExecContext(ctx, `INSERT INTO mail_events(account_id,generation,source,scope,signature,revision) VALUES(?,?,?,?,?,?)
	 ON CONFLICT(account_id,generation,source,scope) DO UPDATE SET signature=excluded.signature,revision=excluded.revision`, session.Account, session.Generation, source, scope, signature, revision); err != nil {
		return false, err
	}
	if len(payload) > 0 {
		var fields map[string]json.RawMessage
		if json.Unmarshal(payload, &fields) != nil || fields == nil {
			return false, errors.New("invalid notification payload")
		}
		fields["event_scope"], _ = json.Marshal(scope)
		fields["event_revision"], _ = json.Marshal(revision)
		payload, err = json.Marshal(fields)
		if err != nil {
			return false, err
		}
		dedupe := scope + ":" + strconv.FormatInt(revision, 10)
		encoded, _ := json.Marshal([]any{session.Account, session.Generation, source, dedupe})
		digest := sha256.Sum256(encoded)
		now := time.Now().UnixMilli()
		if _, err := transaction.ExecContext(ctx, `INSERT INTO mail_outbox(id,account_id,generation,source,dedupe_key,payload,next_attempt,created_at) VALUES(?,?,?,?,?,?,?,?)`, hex.EncodeToString(digest[:]), session.Account, session.Generation, source, dedupe, []byte(payload), now, now); err != nil {
			return false, err
		}
	}
	return len(payload) > 0, transaction.Commit()
}

func (instance *Store) MailEventCurrent(ctx context.Context, session Session, source, scope string, revision int64) (bool, error) {
	var current int64
	err := instance.database.QueryRowContext(ctx, `SELECT revision FROM mail_events WHERE account_id=? AND generation=? AND source=? AND scope=?`, session.Account, session.Generation, source, scope).Scan(&current)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	return current == revision, err
}
