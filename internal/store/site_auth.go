package store

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"time"
	"unicode/utf8"

	"golang.org/x/crypto/scrypt"
)

type siteCredential struct {
	Salt []byte `json:"salt"`
	Hash []byte `json:"hash"`
}

func validSitePurpose(purpose string) bool { return purpose == "access" || purpose == "admin" }

func (instance *Store) SitePasswordConfigured(ctx context.Context, purpose string) (bool, error) {
	if !validSitePurpose(purpose) {
		return false, ErrAccess
	}
	payload, err := instance.SiteSetting(ctx, "credential-"+purpose)
	if errors.Is(err, ErrMissing) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var credential siteCredential
	if json.Unmarshal(payload, &credential) != nil || len(credential.Salt) != 16 || len(credential.Hash) != 32 {
		return false, errors.New("invalid site credential")
	}
	return true, nil
}

func (instance *Store) SetSitePassword(ctx context.Context, purpose, password string, replace bool) error {
	return instance.setSitePasswords(ctx, map[string]string{purpose: password}, replace)
}

func (instance *Store) InitializeSitePasswords(ctx context.Context, passwords map[string]string) error {
	return instance.setSitePasswords(ctx, passwords, false)
}

func (instance *Store) setSitePasswords(ctx context.Context, passwords map[string]string, replace bool) error {
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	for purpose, password := range passwords {
		if err := writeSitePassword(ctx, transaction, purpose, password, replace); err != nil {
			return err
		}
	}
	return transaction.Commit()
}

func writeSitePassword(ctx context.Context, transaction *sql.Tx, purpose, password string, replace bool) error {
	if !validSitePurpose(purpose) || utf8.RuneCountInString(password) < 8 || utf8.RuneCountInString(password) > 256 {
		return errors.New("site password must contain 8 to 256 characters")
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return err
	}
	hash, err := scrypt.Key([]byte(password), salt, 1<<14, 8, 1, 32)
	if err != nil {
		return err
	}
	payload, _ := json.Marshal(siteCredential{Salt: salt, Hash: hash})
	statement := `INSERT INTO site_settings(setting_key,value) VALUES(?,?) ON CONFLICT DO NOTHING`
	if replace {
		statement = `INSERT INTO site_settings(setting_key,value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET value=excluded.value`
	}
	result, err := transaction.ExecContext(ctx, statement, "credential-"+purpose, payload)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrConflict
	}
	if _, err := transaction.ExecContext(ctx, `DELETE FROM site_auth_sessions WHERE purpose=?`, purpose); err != nil {
		return err
	}
	return nil
}

func siteSessionHash(token string, binding []string) string {
	if len(binding) != 0 && binding[0] != "" {
		return tokenHash(token + ":" + binding[0])
	}
	return tokenHash(token)
}

func (instance *Store) CreateSiteSession(ctx context.Context, purpose, password string, lifetime time.Duration, binding ...string) (string, error) {
	if !validSitePurpose(purpose) || utf8.RuneCountInString(password) > 256 || lifetime <= 0 || lifetime > 7*24*time.Hour {
		return "", ErrAccess
	}
	payload, err := instance.SiteSetting(ctx, "credential-"+purpose)
	if err != nil {
		return "", ErrAccess
	}
	var credential siteCredential
	if json.Unmarshal(payload, &credential) != nil || len(credential.Salt) != 16 || len(credential.Hash) != 32 {
		return "", ErrAccess
	}
	hash, err := scrypt.Key([]byte(password), credential.Salt, 1<<14, 8, 1, 32)
	if err != nil || subtle.ConstantTimeCompare(hash, credential.Hash) != 1 {
		return "", ErrAccess
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return "", err
	}
	token := base64.RawURLEncoding.EncodeToString(secret)
	if _, err := instance.database.ExecContext(ctx, `DELETE FROM site_auth_sessions WHERE expires_at<=?`, time.Now().Unix()); err != nil {
		return "", err
	}
	result, err := instance.database.ExecContext(ctx, `INSERT INTO site_auth_sessions(token_hash,purpose,credential,expires_at)
	 SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM site_settings WHERE setting_key=? AND value=?)`, siteSessionHash(token, binding), purpose, payload, time.Now().Add(lifetime).Unix(), "credential-"+purpose, payload)
	if err != nil {
		return "", err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return "", ErrAccess
	}
	return token, nil
}

func (instance *Store) SiteSession(ctx context.Context, purpose, token string, binding ...string) bool {
	if !validSitePurpose(purpose) || len(token) != 43 {
		return false
	}
	var count int
	err := instance.database.QueryRowContext(ctx, `SELECT count(*) FROM site_auth_sessions s JOIN site_settings c ON c.setting_key=? AND c.value=s.credential
	 WHERE s.token_hash=? AND s.purpose=? AND s.expires_at>?`, "credential-"+purpose, siteSessionHash(token, binding), purpose, time.Now().Unix()).Scan(&count)
	return err == nil && count == 1
}

func (instance *Store) RevokeSiteSession(ctx context.Context, purpose, token string, binding ...string) error {
	if !validSitePurpose(purpose) {
		return ErrAccess
	}
	_, err := instance.database.ExecContext(ctx, `DELETE FROM site_auth_sessions WHERE purpose=? AND token_hash=?`, purpose, siteSessionHash(token, binding))
	return err
}

func (instance *Store) RevokeBrowserSiteSessions(ctx context.Context, access, admin string, binding ...string) error {
	_, err := instance.database.ExecContext(ctx, `DELETE FROM site_auth_sessions WHERE
		(purpose='access' AND token_hash=?) OR (purpose='admin' AND token_hash=?)`, tokenHash(access), siteSessionHash(admin, binding))
	return err
}
