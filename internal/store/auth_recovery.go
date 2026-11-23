package store

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"strings"
	"time"
)

type RecoveryContext struct {
	ID, Account, Target, Source, Scope, Epoch, State string
	Generation, Created, Expires                     int64
}

func (instance *Store) recoveryToken(id string) string {
	signer := hmac.New(sha256.New, instance.key)
	signer.Write([]byte("neu-toolbox/auth-recovery/v1/" + id))
	return id + "." + hex.EncodeToString(signer.Sum(nil))
}

func recoveryHash(token string) string {
	digest := sha256.Sum256([]byte(token))
	return hex.EncodeToString(digest[:])
}

func scanRecovery(row interface{ Scan(...any) error }) (RecoveryContext, error) {
	var result RecoveryContext
	err := row.Scan(&result.ID, &result.Account, &result.Generation, &result.Target, &result.Source, &result.Scope, &result.Epoch, &result.Created, &result.Expires, &result.State)
	if errors.Is(err, sql.ErrNoRows) {
		err = ErrMissing
	}
	return result, err
}

const recoveryColumns = `r.id,r.account_id,r.generation,r.target,r.source,r.scope,r.config_epoch,r.created_at,r.expires_at,r.state`
const recoveryCurrent = ` JOIN accounts a ON a.id=r.account_id
 JOIN documents d ON d.account_id=r.account_id AND d.namespace='auth-recovery' AND d.document_key='config'
 WHERE a.enabled=1 AND a.generation=r.generation AND d.deleted=0
 AND json_extract(d.payload,'$.epoch')=r.config_epoch AND json_extract(d.payload,'$.public_base_url')<>''`

func (instance *Store) CreateRecoveryContext(ctx context.Context, session Session, target, source, scope, epoch string, lifetime time.Duration) (RecoveryContext, error) {
	if (target != "primary" && target != "jwxk") || source == "" || len(source) > 64 || scope == "" || len(scope) > 255 || epoch == "" || lifetime < time.Hour || lifetime > 168*time.Hour {
		return RecoveryContext{}, ErrConflict
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return RecoveryContext{}, err
	}
	defer transaction.Rollback()
	var valid int
	err = transaction.QueryRowContext(ctx, `SELECT count(*) FROM accounts a JOIN documents d ON d.account_id=a.id
 WHERE a.id=? AND a.enabled=1 AND a.generation=? AND d.namespace='auth-recovery' AND d.document_key='config'
 AND d.deleted=0 AND json_extract(d.payload,'$.epoch')=? AND json_extract(d.payload,'$.public_base_url')<>''`, session.Account, session.Generation, epoch).Scan(&valid)
	if err != nil {
		return RecoveryContext{}, err
	}
	if valid != 1 {
		return RecoveryContext{}, ErrAccess
	}
	now := time.Now().Unix()
	previous, err := scanRecovery(transaction.QueryRowContext(ctx, `SELECT `+recoveryColumns+` FROM auth_recovery_contexts r
 WHERE account_id=? AND generation=? AND target=? AND source=? AND scope=? AND config_epoch=? AND state='active' AND expires_at>?
 ORDER BY created_at DESC LIMIT 1`, session.Account, session.Generation, target, source, scope, epoch, now))
	if err == nil {
		return previous, transaction.Commit()
	}
	if !errors.Is(err, ErrMissing) {
		return RecoveryContext{}, err
	}
	if _, err := transaction.ExecContext(ctx, `DELETE FROM auth_recovery_contexts WHERE account_id=? AND (expires_at<=? OR state<>'active' OR generation<>? OR config_epoch<>?)`, session.Account, now, session.Generation, epoch); err != nil {
		return RecoveryContext{}, err
	}
	var count int
	if err := transaction.QueryRowContext(ctx, `SELECT count(*) FROM auth_recovery_contexts WHERE account_id=?`, session.Account).Scan(&count); err != nil {
		return RecoveryContext{}, err
	}
	if count >= 64 {
		return RecoveryContext{}, ErrConflict
	}
	identifier := make([]byte, 32)
	if _, err := rand.Read(identifier); err != nil {
		return RecoveryContext{}, err
	}
	result := RecoveryContext{ID: hex.EncodeToString(identifier), Account: session.Account, Generation: session.Generation, Target: target, Source: source, Scope: scope, Epoch: epoch, Created: now, Expires: now + int64(lifetime.Seconds()), State: "active"}
	_, err = transaction.ExecContext(ctx, `INSERT INTO auth_recovery_contexts(id,token_hash,account_id,generation,target,source,scope,config_epoch,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)`,
		result.ID, recoveryHash(instance.recoveryToken(result.ID)), result.Account, result.Generation, result.Target, result.Source, result.Scope, result.Epoch, result.Created, result.Expires)
	if err != nil {
		return RecoveryContext{}, err
	}
	return result, transaction.Commit()
}

func (instance *Store) RecoveryContext(ctx context.Context, token string) (RecoveryContext, error) {
	if len(token) != 129 || token[64] != '.' {
		return RecoveryContext{}, ErrMissing
	}
	if _, err := hex.DecodeString(token[:64]); err != nil {
		return RecoveryContext{}, ErrMissing
	}
	if !hmac.Equal([]byte(instance.recoveryToken(token[:64])), []byte(token)) {
		return RecoveryContext{}, ErrMissing
	}
	return scanRecovery(instance.database.QueryRowContext(ctx, `SELECT `+recoveryColumns+` FROM auth_recovery_contexts r`+recoveryCurrent+` AND r.token_hash=? AND r.expires_at>? AND r.state='active'`, recoveryHash(token), time.Now().Unix()))
}

func (instance *Store) RecoveryToken(ctx context.Context, id string) (string, error) {
	if len(id) != 64 || strings.ContainsAny(id, ". /\\") {
		return "", ErrMissing
	}
	token := instance.recoveryToken(id)
	if _, err := instance.RecoveryContext(ctx, token); err != nil {
		return "", err
	}
	return token, nil
}

func (instance *Store) FinishRecoveryContext(ctx context.Context, token, state string) error {
	if state != "consumed" && state != "cancelled" {
		return ErrConflict
	}
	recovery, err := instance.RecoveryContext(ctx, token)
	if err != nil {
		return err
	}
	result, err := instance.database.ExecContext(ctx, `UPDATE auth_recovery_contexts SET state=? WHERE id IN
 (SELECT r.id FROM auth_recovery_contexts r`+recoveryCurrent+` AND r.id=? AND r.state='active' AND r.expires_at>?)`, state, recovery.ID, time.Now().Unix())
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrMissing
	}
	return nil
}

func (instance *Store) InvalidateRecoveryContexts(ctx context.Context, session Session, target string) error {
	_, err := instance.database.ExecContext(ctx, `UPDATE auth_recovery_contexts SET state='cancelled' WHERE account_id=? AND generation=? AND target=? AND state='active'`, session.Account, session.Generation, target)
	return err
}

func (instance *Store) ServiceSessionAccounts(ctx context.Context) ([]Session, error) {
	rows, err := instance.database.QueryContext(ctx, `SELECT a.id,a.generation FROM accounts a JOIN secrets s ON s.account_id=a.id AND s.purpose='campus-service-jwxk' WHERE a.enabled=1`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var sessions []Session
	for rows.Next() {
		var session Session
		if err := rows.Scan(&session.Account, &session.Generation); err != nil {
			return nil, err
		}
		sessions = append(sessions, session)
	}
	return sessions, rows.Err()
}
