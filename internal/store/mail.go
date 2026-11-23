package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"
)

type MailEntry struct {
	ID       string
	Session  Session
	Source   string
	Dedupe   string
	Payload  json.RawMessage
	Attempts int
	Claim    string
}

type MailDraft struct {
	Source, Dedupe string
	Payload        json.RawMessage
}

func (draft MailDraft) Valid() bool {
	return draft.Source != "" && len(draft.Source) <= 64 && draft.Dedupe != "" && len(draft.Dedupe) <= 512 && json.Valid(draft.Payload) && len(draft.Payload) <= 1024*1024
}

func insertMailDraft(ctx context.Context, transaction *sql.Tx, session Session, draft MailDraft) error {
	encoded, _ := json.Marshal([]any{session.Account, session.Generation, draft.Source, draft.Dedupe})
	digest := sha256.Sum256(encoded)
	now := time.Now().UnixMilli()
	_, err := transaction.ExecContext(ctx, `INSERT INTO mail_outbox(id,account_id,generation,source,dedupe_key,payload,next_attempt,created_at)
	 VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id,generation,source,dedupe_key) DO NOTHING`, hex.EncodeToString(digest[:]), session.Account, session.Generation, draft.Source, draft.Dedupe, []byte(draft.Payload), now, now)
	return err
}

func (instance *Store) QueueMail(ctx context.Context, session Session, source, dedupe string, payload json.RawMessage) (bool, error) {
	if source == "" || len(source) > 64 || dedupe == "" || len(dedupe) > 512 || !json.Valid(payload) || len(payload) > 1024*1024 {
		return false, errors.New("invalid mail entry")
	}
	encoded, _ := json.Marshal([]any{session.Account, session.Generation, source, dedupe})
	digest := sha256.Sum256(encoded)
	now := time.Now().UnixMilli()
	result, err := instance.database.ExecContext(ctx, `INSERT INTO mail_outbox(id,account_id,generation,source,dedupe_key,payload,next_attempt,created_at)
	 SELECT ?,id,generation,?,?,?,?,? FROM accounts WHERE id=? AND enabled=1 AND generation=?
	 ON CONFLICT(account_id,generation,source,dedupe_key) DO NOTHING`, hex.EncodeToString(digest[:]), source, dedupe, []byte(payload), now, now, session.Account, session.Generation)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return false, err
	}
	if count == 0 {
		generation, err := instance.Generation(ctx, session.Account)
		if err != nil {
			return false, err
		}
		if generation != session.Generation {
			return false, ErrAccess
		}
	}
	return count == 1, nil
}

func (instance *Store) DueMailAccounts(ctx context.Context, now time.Time) ([]Session, error) {
	if _, err := instance.database.ExecContext(ctx, `UPDATE mail_outbox SET state='cancelled',payload='{}',claim='',lease_until=0
	 WHERE state IN ('queued','sending') AND NOT EXISTS(SELECT 1 FROM accounts WHERE id=mail_outbox.account_id AND enabled=1 AND generation=mail_outbox.generation)`); err != nil {
		return nil, err
	}
	rows, err := instance.database.QueryContext(ctx, `SELECT DISTINCT account_id,generation FROM mail_outbox
	 WHERE state='queued' AND next_attempt<=? OR state='sending' AND lease_until<=? LIMIT 64`, now.UnixMilli(), now.UnixMilli())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	sessions := []Session{}
	for rows.Next() {
		var session Session
		if err := rows.Scan(&session.Account, &session.Generation); err != nil {
			return nil, err
		}
		sessions = append(sessions, session)
	}
	return sessions, rows.Err()
}

func (instance *Store) ClaimMail(ctx context.Context, session Session, now time.Time) (MailEntry, error) {
	entry := MailEntry{Session: session}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return entry, err
	}
	defer transaction.Rollback()
	err = transaction.QueryRowContext(ctx, `SELECT id,source,dedupe_key,payload,attempts FROM mail_outbox
	 WHERE account_id=? AND generation=? AND ((state='queued' AND next_attempt<=?) OR (state='sending' AND lease_until<=?))
	 AND EXISTS(SELECT 1 FROM accounts WHERE id=? AND enabled=1 AND generation=?) ORDER BY created_at,id LIMIT 1`, session.Account, session.Generation, now.UnixMilli(), now.UnixMilli(), session.Account, session.Generation).Scan(&entry.ID, &entry.Source, &entry.Dedupe, &entry.Payload, &entry.Attempts)
	if errors.Is(err, sql.ErrNoRows) {
		return entry, ErrMissing
	}
	if err != nil {
		return entry, err
	}
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return entry, err
	}
	entry.Claim = hex.EncodeToString(identifier)
	entry.Attempts++
	if _, err := transaction.ExecContext(ctx, `UPDATE mail_outbox SET state='sending',attempts=?,claim=?,lease_until=? WHERE id=?`, entry.Attempts, entry.Claim, now.Add(time.Minute).UnixMilli(), entry.ID); err != nil {
		return entry, err
	}
	return entry, transaction.Commit()
}

func (instance *Store) FinishMail(ctx context.Context, entry MailEntry, delivered bool, errorKind string, now time.Time) error {
	state := "queued"
	if delivered {
		state = "sent"
	}
	if errorKind != "" {
		errorKind = "delivery_failed"
	}
	delay := time.Minute * time.Duration(1<<min(max(entry.Attempts-1, 0), 6))
	sentAt := int64(0)
	if delivered {
		sentAt = now.UnixMilli()
	}
	result, err := instance.database.ExecContext(ctx, `UPDATE mail_outbox SET state=?,payload=CASE WHEN ? THEN '{}' ELSE payload END,
	 error_kind=?,next_attempt=?,sent_at=?,claim='',lease_until=0 WHERE id=? AND claim=? AND state='sending'
	 AND EXISTS(SELECT 1 FROM accounts WHERE id=? AND enabled=1 AND generation=?)`, state, delivered, errorKind, now.Add(delay).UnixMilli(), sentAt, entry.ID, entry.Claim, entry.Session.Account, entry.Session.Generation)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrConflict
	}
	return nil
}

func (instance *Store) PendingMailCount(ctx context.Context, session Session, source string) (int, error) {
	var count int
	err := instance.database.QueryRowContext(ctx, `SELECT count(*) FROM mail_outbox WHERE account_id=? AND generation=? AND state IN ('queued','sending') AND (?='' OR source=?)`, session.Account, session.Generation, source, source).Scan(&count)
	return count, err
}

func (instance *Store) MailDelivered(ctx context.Context, session Session, source, dedupe string) (bool, error) {
	var count int
	err := instance.database.QueryRowContext(ctx, `SELECT count(*) FROM mail_outbox WHERE account_id=? AND generation=? AND source=? AND dedupe_key=? AND state='sent'`, session.Account, session.Generation, source, dedupe).Scan(&count)
	return count == 1, err
}

func (instance *Store) LastMailDelivery(ctx context.Context, session Session, source string) (string, error) {
	var timestamp int64
	err := instance.database.QueryRowContext(ctx, `SELECT COALESCE(MAX(sent_at),0) FROM mail_outbox WHERE account_id=? AND generation=? AND source=? AND state='sent'`, session.Account, session.Generation, source).Scan(&timestamp)
	if err != nil || timestamp == 0 {
		return "", err
	}
	return time.UnixMilli(timestamp).UTC().Format(time.RFC3339Nano), nil
}

func (instance *Store) DiscardMail(ctx context.Context, session Session, source, dedupe string) error {
	if source == "" {
		return errors.New("mail source is required")
	}
	_, err := instance.database.ExecContext(ctx, `UPDATE mail_outbox SET state='cancelled',payload='{}',claim='',lease_until=0
	 WHERE account_id=? AND generation=? AND source=? AND (?='' OR dedupe_key=?) AND state IN ('queued','sending')`, session.Account, session.Generation, source, dedupe, dedupe)
	return err
}

func (instance *Store) MailClaimActive(ctx context.Context, entry MailEntry) (bool, error) {
	var count int
	err := instance.database.QueryRowContext(ctx, `SELECT count(*) FROM mail_outbox m JOIN accounts a ON a.id=m.account_id
	 WHERE m.id=? AND m.claim=? AND m.state='sending' AND a.enabled=1 AND a.generation=m.generation`, entry.ID, entry.Claim).Scan(&count)
	return count == 1, err
}
