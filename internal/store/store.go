package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

var ErrConflict = errors.New("document revision conflict")
var ErrAccess = errors.New("account is not active")
var ErrMissing = errors.New("document not found")

type Store struct {
	database *sql.DB
	key      []byte
}

const currentSchemaVersion = 11

type Document struct {
	Key      string          `json:"key"`
	Payload  json.RawMessage `json:"data"`
	Revision int64           `json:"revision"`
	Deleted  bool            `json:"deleted"`
	Updated  string          `json:"updated_at"`
}

func Open(directory string) (*Store, error) {
	if _, err := os.Lstat(filepath.Join(directory, ".restore-in-progress")); err == nil {
		return nil, errors.New("restore is incomplete; select a successfully restored data directory")
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	database, err := sql.Open("sqlite", filepath.Join(directory, "toolbox.db"))
	if err != nil {
		return nil, err
	}
	database.SetMaxOpenConns(1)
	instance := &Store{database: database}
	if err := instance.initialize(directory); err != nil {
		database.Close()
		return nil, err
	}
	return instance, nil
}

func (instance *Store) initialize(directory string) error {
	for _, statement := range []string{"PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000"} {
		if _, err := instance.database.Exec(statement); err != nil {
			return err
		}
	}
	var version int
	if err := instance.database.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if version > currentSchemaVersion {
		return errors.New("database requires a newer toolbox version")
	}
	transaction, err := instance.database.Begin()
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	for _, statement := range []string{
		`CREATE TABLE IF NOT EXISTS accounts (
		 id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
		 generation INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS documents (
		 account_id TEXT NOT NULL REFERENCES accounts(id), namespace TEXT NOT NULL,
		 document_key TEXT NOT NULL, payload BLOB NOT NULL, revision INTEGER NOT NULL,
		 deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
		 PRIMARY KEY(account_id, namespace, document_key))`,
		`CREATE TABLE IF NOT EXISTS sessions (
		 token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
		 generation INTEGER NOT NULL, expires_at INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS secrets (
		 account_id TEXT NOT NULL REFERENCES accounts(id), purpose TEXT NOT NULL,
		 ciphertext BLOB NOT NULL, PRIMARY KEY(account_id,purpose))`,
		`CREATE TABLE IF NOT EXISTS site_settings (setting_key TEXT PRIMARY KEY, value BLOB NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS diagnostic_logs (
		 id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
		 category TEXT NOT NULL, day TEXT NOT NULL, timestamp TEXT NOT NULL, payload BLOB NOT NULL)`,
		`CREATE INDEX IF NOT EXISTS diagnostic_logs_scope ON diagnostic_logs(account_id,day,category,id)`,
		`CREATE INDEX IF NOT EXISTS diagnostic_logs_day ON diagnostic_logs(day)`,
		`CREATE TABLE IF NOT EXISTS site_auth_sessions (
		 token_hash TEXT PRIMARY KEY, purpose TEXT NOT NULL, credential BLOB NOT NULL, expires_at INTEGER NOT NULL)`,
		`CREATE TABLE IF NOT EXISTS auth_recovery_contexts (
		 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL REFERENCES accounts(id),
		 generation INTEGER NOT NULL, target TEXT NOT NULL, source TEXT NOT NULL, scope TEXT NOT NULL,
		 config_epoch TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
		 state TEXT NOT NULL DEFAULT 'active')`,
		`CREATE INDEX IF NOT EXISTS auth_recovery_account ON auth_recovery_contexts(account_id,generation,state)`,
		`CREATE TABLE IF NOT EXISTS cache_entries (
		 account_id TEXT NOT NULL REFERENCES accounts(id), resource TEXT NOT NULL, variant TEXT NOT NULL,
		 schema_version INTEGER NOT NULL, payload BLOB NOT NULL, revision TEXT NOT NULL,
		 saved_at TEXT NOT NULL, checked_at TEXT NOT NULL, attempted_at TEXT NOT NULL,
		 error_kind TEXT NOT NULL DEFAULT '', invalidated INTEGER NOT NULL DEFAULT 0,
		 dependencies BLOB NOT NULL DEFAULT '{}', PRIMARY KEY(account_id,resource,variant))`,
		`CREATE TABLE IF NOT EXISTS cache_events (
		 cursor INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL REFERENCES accounts(id),
		 resource TEXT NOT NULL, variant TEXT NOT NULL, previous_revision TEXT NOT NULL,
		 revision TEXT NOT NULL, changed INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)`,
		`CREATE INDEX IF NOT EXISTS cache_events_account_cursor ON cache_events(account_id,cursor)`,
		`CREATE TABLE IF NOT EXISTS evaluation_attempts (
		 account_id TEXT NOT NULL REFERENCES accounts(id), operation_key TEXT NOT NULL,
		 state TEXT NOT NULL, attempt INTEGER NOT NULL, updated_at TEXT NOT NULL,
		 PRIMARY KEY(account_id,operation_key))`,
		`CREATE TABLE IF NOT EXISTS mail_outbox (
		 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), generation INTEGER NOT NULL,
		 source TEXT NOT NULL, dedupe_key TEXT NOT NULL, payload BLOB NOT NULL,
		 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
		 next_attempt INTEGER NOT NULL, lease_until INTEGER NOT NULL DEFAULT 0,
		 claim TEXT NOT NULL DEFAULT '', error_kind TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
		 UNIQUE(account_id,generation,source,dedupe_key))`,
		`CREATE INDEX IF NOT EXISTS mail_outbox_due ON mail_outbox(state,next_attempt,lease_until)`,
		`CREATE TABLE IF NOT EXISTS mail_events (
		 account_id TEXT NOT NULL REFERENCES accounts(id), generation INTEGER NOT NULL, source TEXT NOT NULL,
		 scope TEXT NOT NULL, signature TEXT NOT NULL, revision INTEGER NOT NULL,
		 PRIMARY KEY(account_id,generation,source,scope))`,
	} {
		if _, err := transaction.Exec(statement); err != nil {
			return err
		}
	}
	if version < 4 {
		if _, err := transaction.Exec(`ALTER TABLE cache_events ADD COLUMN changes BLOB NOT NULL DEFAULT '{}'`); err != nil {
			return err
		}
	}
	if version < 8 {
		if _, err := transaction.Exec(`ALTER TABLE mail_outbox ADD COLUMN sent_at INTEGER NOT NULL DEFAULT 0`); err != nil {
			return err
		}
	}
	if _, err := transaction.Exec(fmt.Sprintf("PRAGMA user_version=%d", currentSchemaVersion)); err != nil {
		return err
	}
	if err := os.Chmod(filepath.Join(directory, "toolbox.db"), 0600); err != nil {
		return err
	}
	if err := instance.openKey(directory, transaction); err != nil {
		return err
	}
	if version < currentSchemaVersion {
		if err := instance.verifySecretKey(context.Background(), transaction); err != nil {
			return err
		}
	}
	return transaction.Commit()
}

func (instance *Store) Close() error { return instance.database.Close() }

func (instance *Store) AddAccount(ctx context.Context, account string) error {
	if strings.TrimSpace(account) != account || account == "" || len(account) > 64 {
		return errors.New("invalid account")
	}
	_, err := instance.database.ExecContext(ctx,
		"INSERT INTO accounts(id,created_at) VALUES(?,?) ON CONFLICT(id) DO NOTHING", account, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

func (instance *Store) SetEnabled(ctx context.Context, account string, enabled bool) error {
	return instance.SetAdmission(ctx, account, enabled, false)
}

func (instance *Store) Generation(ctx context.Context, account string) (int64, error) {
	var generation int64
	err := instance.database.QueryRowContext(ctx,
		"SELECT generation FROM accounts WHERE id=? AND enabled=1", account).Scan(&generation)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrAccess
	}
	return generation, err
}

func validDocument(namespace, key string) bool {
	if key == "" || len(key) > 255 || strings.ContainsAny(key, "\x00\r\n") {
		return false
	}
	switch namespace {
	case "grade-tracking", "auth-recovery", "experiment-operations", "research-operations":
		return true
	case "gpa", "gpa-policy", "agenda", "selection-plans", "research-favorites", "theme", "settings", "jwxk-catalog-queries", "jwxk-rounds", "selection-attempts", "jwxk-archives", "weight-applications", "jwxk-automation-settings", "jwxk-automation-tasks":
		return true
	}
	return false
}

func (instance *Store) Read(ctx context.Context, account, namespace, key string) (Document, error) {
	if !validDocument(namespace, key) {
		return Document{}, errors.New("invalid document key")
	}
	var document Document
	var enabled bool
	err := instance.database.QueryRowContext(ctx, `SELECT a.enabled, d.document_key, d.payload, d.revision, d.deleted, d.updated_at
	 FROM accounts a JOIN documents d ON a.id=d.account_id
	 WHERE a.id=? AND d.namespace=? AND d.document_key=?`, account, namespace, key).
		Scan(&enabled, &document.Key, &document.Payload, &document.Revision, &document.Deleted, &document.Updated)
	if errors.Is(err, sql.ErrNoRows) {
		return Document{}, ErrMissing
	}
	if err != nil {
		return Document{}, err
	}
	if !enabled {
		return Document{}, ErrAccess
	}
	return document, nil
}

func (instance *Store) Save(ctx context.Context, account, namespace, key string, payload json.RawMessage, expected int64, deleted bool) (Document, error) {
	return instance.save(ctx, account, namespace, key, payload, expected, deleted, nil)
}

func (instance *Store) SaveForSession(ctx context.Context, session Session, namespace, key string, payload json.RawMessage, expected int64, deleted bool) (Document, error) {
	return instance.save(ctx, session.Account, namespace, key, payload, expected, deleted, &session.Generation)
}

func (instance *Store) save(ctx context.Context, account, namespace, key string, payload json.RawMessage, expected int64, deleted bool, expectedGeneration *int64) (Document, error) {
	documents, err := instance.saveDocumentBatch(ctx, account, []DocumentChange{{Namespace: namespace, Key: key, Payload: payload, Revision: expected, Deleted: deleted}}, expectedGeneration)
	if err != nil {
		return Document{}, err
	}
	return documents[0], nil
}

type DocumentChange struct {
	Namespace, Key string
	Payload        json.RawMessage
	Revision       int64
	Deleted        bool
}

const MaxDocumentBatchBytes = 128 * 1024 * 1024

func DocumentPayloadLimit(namespace string) int {
	if namespace == "jwxk-archives" {
		return 64 * 1024 * 1024
	}
	return 4 * 1024 * 1024
}

func (instance *Store) SaveBatchForSession(ctx context.Context, session Session, changes []DocumentChange) ([]Document, error) {
	return instance.saveDocumentBatch(ctx, session.Account, changes, &session.Generation)
}

func (instance *Store) saveDocumentBatch(ctx context.Context, account string, changes []DocumentChange, expectedGeneration *int64) ([]Document, error) {
	return instance.saveDocumentBatchWithMail(ctx, account, changes, expectedGeneration, nil)
}

func (instance *Store) SaveBatchWithMail(ctx context.Context, session Session, changes []DocumentChange, mail *MailDraft) ([]Document, error) {
	return instance.saveDocumentBatchWithMail(ctx, session.Account, changes, &session.Generation, mail)
}

func (instance *Store) saveDocumentBatchWithMail(ctx context.Context, account string, changes []DocumentChange, expectedGeneration *int64, mail *MailDraft) ([]Document, error) {
	return instance.saveDocumentBatchWithImportedMail(ctx, account, changes, expectedGeneration, mail, nil)
}

func (instance *Store) ImportSettingsForSession(ctx context.Context, session Session, changes []DocumentChange, mailConfig []byte) ([]Document, error) {
	return instance.saveDocumentBatchWithImportedMail(ctx, session.Account, changes, &session.Generation, nil, mailConfig)
}

func (instance *Store) saveDocumentBatchWithImportedMail(ctx context.Context, account string, changes []DocumentChange, expectedGeneration *int64, mail *MailDraft, importedMail []byte) ([]Document, error) {
	if len(changes) == 0 || len(changes) > 256 || len(importedMail) > 512*1024 || importedMail != nil && !json.Valid(importedMail) {
		return nil, errors.New("invalid document batch")
	}
	seen := map[string]bool{}
	total := 0
	for _, change := range changes {
		identity := change.Namespace + "\x00" + change.Key
		total += len(change.Payload)
		if seen[identity] || !validDocument(change.Namespace, change.Key) || change.Revision < 0 || len(change.Payload) > DocumentPayloadLimit(change.Namespace) || !json.Valid(change.Payload) || total > MaxDocumentBatchBytes {
			return nil, errors.New("invalid document")
		}
		seen[identity] = true
	}
	if mail != nil && !mail.Valid() {
		return nil, errors.New("invalid mail draft")
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer transaction.Rollback()
	var enabled bool
	var generation int64
	err = transaction.QueryRowContext(ctx, "SELECT enabled,generation FROM accounts WHERE id=?", account).Scan(&enabled, &generation)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && (!enabled || (expectedGeneration != nil && generation != *expectedGeneration))) {
		return nil, ErrAccess
	}
	if err != nil {
		return nil, err
	}
	documents := make([]Document, 0, len(changes))
	for _, change := range changes {
		namespace, key, payload, expected, deleted := change.Namespace, change.Key, change.Payload, change.Revision, change.Deleted
		if deleted {
			payload = json.RawMessage("null")
		}
		document := Document{Key: key, Payload: payload, Revision: expected + 1, Deleted: deleted, Updated: time.Now().UTC().Format(time.RFC3339Nano)}
		var result sql.Result
		if expected == 0 {
			result, err = transaction.ExecContext(ctx, `INSERT INTO documents(account_id,namespace,document_key,payload,revision,deleted,updated_at)
		 VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING`, account, namespace, key, []byte(payload), document.Revision, deleted, document.Updated)
		} else {
			result, err = transaction.ExecContext(ctx, `UPDATE documents SET payload=?,revision=?,deleted=?,updated_at=?
		 WHERE account_id=? AND namespace=? AND document_key=? AND revision=?`,
				[]byte(payload), document.Revision, deleted, document.Updated, account, namespace, key, expected)
		}
		if err != nil {
			return nil, fmt.Errorf("save document: %w", err)
		}
		if count, err := result.RowsAffected(); err != nil || count != 1 {
			return nil, ErrConflict
		}
		documents = append(documents, document)
	}
	if mail != nil {
		if err := insertMailDraft(ctx, transaction, Session{Account: account, Generation: generation}, *mail); err != nil {
			return nil, err
		}
	}
	if importedMail != nil {
		ciphertext, err := instance.encryptSecret(account, "mail-config", importedMail)
		if err != nil {
			return nil, err
		}
		result, err := transaction.ExecContext(ctx, `INSERT INTO secrets(account_id,purpose,ciphertext) VALUES(?,'mail-config',?) ON CONFLICT DO NOTHING`, account, ciphertext)
		if err != nil {
			return nil, err
		}
		if count, err := result.RowsAffected(); err != nil || count != 1 {
			return nil, ErrConflict
		}
	}
	if err := transaction.Commit(); err != nil {
		return nil, err
	}
	return documents, nil
}
