package store

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"time"
)

type Session struct {
	Account    string
	Generation int64
	Expires    time.Time
}

func tokenHash(token string) string {
	digest := sha256.Sum256([]byte(token))
	return hex.EncodeToString(digest[:])
}

func (instance *Store) CreateSession(ctx context.Context, account string, lifetime time.Duration) (string, error) {
	generation, err := instance.Generation(ctx, account)
	if err != nil {
		return "", err
	}
	return instance.CreateSessionAtGeneration(ctx, account, generation, lifetime)
}

func (instance *Store) CreateSessionAtGeneration(ctx context.Context, account string, generation int64, lifetime time.Duration) (string, error) {
	if lifetime <= 0 || lifetime > 30*24*time.Hour {
		return "", errors.New("invalid session lifetime")
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return "", err
	}
	token := base64.RawURLEncoding.EncodeToString(secret)
	result, err := instance.database.ExecContext(ctx, `INSERT INTO sessions(token_hash,account_id,generation,expires_at)
	 SELECT ?,id,generation,? FROM accounts WHERE id=? AND enabled=1 AND generation=?`, tokenHash(token), time.Now().Add(lifetime).Unix(), account, generation)
	if err != nil {
		return "", err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return "", ErrAccess
	}
	return token, nil
}

func (instance *Store) Session(ctx context.Context, token string) (Session, error) {
	if len(token) != 43 {
		return Session{}, ErrAccess
	}
	var session Session
	var expires int64
	err := instance.database.QueryRowContext(ctx, `SELECT s.account_id,s.generation,s.expires_at FROM sessions s
	 JOIN accounts a ON s.account_id=a.id WHERE s.token_hash=? AND s.expires_at>? AND a.enabled=1 AND s.generation=a.generation`,
		tokenHash(token), time.Now().Unix()).Scan(&session.Account, &session.Generation, &expires)
	if errors.Is(err, sql.ErrNoRows) {
		return Session{}, ErrAccess
	}
	session.Expires = time.Unix(expires, 0)
	return session, err
}

func (instance *Store) RevokeSession(ctx context.Context, token string) error {
	_, err := instance.database.ExecContext(ctx, "DELETE FROM sessions WHERE token_hash=?", tokenHash(token))
	return err
}

func (instance *Store) RevokeAccount(ctx context.Context, account string) error {
	return instance.RevokeAccountSession(ctx, Session{Account: account}, false)
}

func (instance *Store) RevokeAccountSession(ctx context.Context, session Session, clearData bool) error {
	return instance.EndAccountSession(ctx, session, clearData, false)
}

func (instance *Store) EndAccountSession(ctx context.Context, session Session, clearData, forgetCredentials bool) error {
	account := session.Account
	if clearData && session.Generation <= 0 {
		return ErrAccess
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	result, err := transaction.ExecContext(ctx, "UPDATE accounts SET generation=generation+1 WHERE id=? AND (?=0 OR generation=?)", account, session.Generation, session.Generation)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrMissing
	}
	if err := pauseAccountDocuments(ctx, transaction, account); err != nil {
		return err
	}
	if forgetCredentials {
		if _, err := transaction.ExecContext(ctx, `DELETE FROM secrets WHERE account_id=? AND purpose IN ('campus-session','campus-service-jwxk')`, account); err != nil {
			return err
		}
	}
	if clearData {
		for _, statement := range []string{"DELETE FROM cache_entries WHERE account_id=?", "DELETE FROM cache_events WHERE account_id=?", "DELETE FROM diagnostic_logs WHERE account_id=?"} {
			if _, err := transaction.ExecContext(ctx, statement, account); err != nil {
				return err
			}
		}
	}
	return transaction.Commit()
}

func (instance *Store) SiteSetting(ctx context.Context, key string) ([]byte, error) {
	var value []byte
	err := instance.database.QueryRowContext(ctx, "SELECT value FROM site_settings WHERE setting_key=?", key).Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrMissing
	}
	return value, err
}

func (instance *Store) InitializeSetting(ctx context.Context, key string, value []byte) error {
	_, err := instance.database.ExecContext(ctx, "INSERT INTO site_settings(setting_key,value) VALUES(?,?) ON CONFLICT DO NOTHING", key, value)
	return err
}

func (instance *Store) InitializeDeploymentMode(ctx context.Context, mode string) error {
	_, err := instance.database.ExecContext(ctx, `INSERT INTO site_settings(setting_key,value)
		SELECT 'deployment-mode', ? WHERE ? = 'remote-multi' OR NOT EXISTS (SELECT 1 FROM accounts LIMIT 1 OFFSET 1)
		ON CONFLICT DO NOTHING`, []byte(mode), mode)
	return err
}

func (instance *Store) RestorableAccounts(ctx context.Context) ([]Session, error) {
	rows, err := instance.database.QueryContext(ctx, `SELECT a.id,a.generation FROM accounts a
	 JOIN secrets c ON c.account_id=a.id AND c.purpose='campus-session'
	 WHERE a.enabled=1 AND EXISTS(SELECT 1 FROM sessions s WHERE s.account_id=a.id AND s.generation=a.generation AND s.expires_at>?)`, time.Now().Unix())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Session{}
	for rows.Next() {
		var session Session
		if err := rows.Scan(&session.Account, &session.Generation); err != nil {
			return nil, err
		}
		result = append(result, session)
	}
	return result, rows.Err()
}
