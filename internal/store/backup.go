package store

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"filippo.io/age"
)

const backupDatabaseLimit = 512 * 1024 * 1024
const backupArchiveLimit = backupDatabaseLimit + 1024*1024

type BackupManifest struct {
	Format         int    `json:"format"`
	Schema         int    `json:"schema"`
	CreatedAt      string `json:"created_at"`
	DatabaseSHA256 string `json:"database_sha256"`
	KeySHA256      string `json:"key_sha256"`
}

func openBackupReadOnly(filename string) (*sql.DB, error) {
	path, err := filepath.Abs(filename)
	if err != nil {
		return nil, err
	}
	path = filepath.ToSlash(path)
	if path[0] != '/' {
		path = "/" + path
	}
	source := url.URL{Scheme: "file", Path: path, RawQuery: "mode=ro"}
	return sql.Open("sqlite", source.String())
}

func BackupDirectory(ctx context.Context, directory, output, password string) error {
	if _, err := os.Lstat(filepath.Join(directory, ".restore-in-progress")); !errors.Is(err, os.ErrNotExist) {
		return errors.New("cannot back up incomplete restore")
	}
	database, err := openBackupReadOnly(filepath.Join(directory, "toolbox.db"))
	if err != nil {
		return err
	}
	defer database.Close()
	key, err := os.ReadFile(filepath.Join(directory, "vault.key"))
	if err != nil || len(key) != 32 {
		return errors.New("invalid backup vault key")
	}
	instance := &Store{database: database, key: key}
	var version int
	if err := database.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if version < 1 || version > currentSchemaVersion {
		return errors.New("unsupported backup schema")
	}
	return instance.Backup(ctx, output, password)
}

func CheckUpgrade(ctx context.Context, directory string) (result error) {
	stage, err := CreateUpgradeSnapshot(ctx, directory, os.TempDir())
	if err != nil {
		return err
	}
	defer func() { result = errors.Join(result, os.RemoveAll(stage)) }()
	checked, err := Open(stage)
	if err != nil {
		return err
	}
	return checked.Close()
}

func CreateUpgradeSnapshot(ctx context.Context, directory, parent string) (string, error) {
	if _, err := os.Lstat(filepath.Join(directory, ".restore-in-progress")); !errors.Is(err, os.ErrNotExist) {
		return "", errors.New("cannot check an incomplete restore")
	}
	key, err := os.ReadFile(filepath.Join(directory, "vault.key"))
	if err != nil || len(key) != 32 {
		return "", errors.New("invalid upgrade source key")
	}
	source, err := openBackupReadOnly(filepath.Join(directory, "toolbox.db"))
	if err != nil {
		return "", err
	}
	defer source.Close()
	stage, err := os.MkdirTemp(parent, ".toolbox-upgrade-snapshot-")
	if err != nil {
		return "", err
	}
	fail := func(err error) (string, error) { return "", errors.Join(err, os.RemoveAll(stage)) }
	snapshot := filepath.Join(stage, "toolbox.db")
	if _, err := source.ExecContext(ctx, "VACUUM INTO ?", snapshot); err != nil {
		return fail(err)
	}
	if err := os.Chmod(snapshot, 0600); err != nil {
		return fail(err)
	}
	if err := os.WriteFile(filepath.Join(stage, "vault.key"), key, 0600); err != nil {
		return fail(err)
	}
	checked, err := openBackupReadOnly(snapshot)
	if err != nil {
		return fail(err)
	}
	instance := &Store{key: key}
	if err := errors.Join(instance.verifySecretKey(ctx, checked), checked.Close()); err != nil {
		return fail(err)
	}
	return stage, nil
}

func backupPassword(password string) error {
	if len(password) < 12 || len(password) > 1024 {
		return errors.New("backup password must contain 12 to 1024 bytes")
	}
	return nil
}

func fileDigest(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func (instance *Store) Backup(ctx context.Context, output, password string) error {
	if err := backupPassword(password); err != nil {
		return err
	}
	absolute, err := filepath.Abs(output)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(absolute); err == nil {
		return errors.New("backup output already exists")
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	stage, err := os.MkdirTemp(filepath.Dir(absolute), ".toolbox-backup-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	snapshot := filepath.Join(stage, "toolbox.db")
	if _, err := instance.database.ExecContext(ctx, "VACUUM INTO ?", snapshot); err != nil {
		return err
	}
	if err := os.Chmod(snapshot, 0600); err != nil {
		return err
	}
	info, err := os.Stat(snapshot)
	if err != nil {
		return err
	}
	if info.Size() > backupDatabaseLimit {
		return errors.New("database exceeds the 512 MiB backup limit")
	}
	databaseHash, err := fileDigest(snapshot)
	if err != nil {
		return err
	}
	keyHash := sha256.Sum256(instance.key)
	var schema int
	snapshotDB, err := openBackupReadOnly(snapshot)
	if err != nil {
		return err
	}
	defer snapshotDB.Close()
	if err := snapshotDB.QueryRowContext(ctx, "PRAGMA user_version").Scan(&schema); err != nil {
		return err
	}
	if schema < 1 || schema > currentSchemaVersion {
		return errors.New("unsupported snapshot schema")
	}
	if err := instance.verifySecretKey(ctx, snapshotDB); err != nil {
		return err
	}
	manifest, err := json.Marshal(BackupManifest{1, schema, time.Now().UTC().Format(time.RFC3339Nano), databaseHash, hex.EncodeToString(keyHash[:])})
	if err != nil {
		return err
	}
	sealedPath := filepath.Join(stage, "backup.age")
	sealed, err := os.OpenFile(sealedPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer sealed.Close()
	recipient, err := age.NewScryptRecipient(password)
	if err != nil {
		return err
	}
	encrypted, err := age.Encrypt(sealed, recipient)
	if err != nil {
		return err
	}
	archive := zip.NewWriter(encrypted)
	add := func(name string, input io.Reader) error {
		header := &zip.FileHeader{Name: name, Method: zip.Deflate}
		header.SetMode(0600)
		entry, err := archive.CreateHeader(header)
		if err != nil {
			return err
		}
		_, err = io.Copy(entry, input)
		return err
	}
	databaseFile, err := os.Open(snapshot)
	if err != nil {
		return err
	}
	err = add("toolbox.db", databaseFile)
	closeErr := databaseFile.Close()
	if err := errors.Join(err, closeErr); err != nil {
		return err
	}
	if err := add("vault.key", bytes.NewReader(instance.key)); err != nil {
		return err
	}
	if err := add("manifest.json", bytes.NewReader(manifest)); err != nil {
		return err
	}
	if err := archive.Close(); err != nil {
		return err
	}
	if err := encrypted.Close(); err != nil {
		return err
	}
	if err := sealed.Sync(); err != nil {
		return err
	}
	if err := sealed.Close(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return os.Link(sealedPath, absolute)
}

func extractBackup(ctx context.Context, input, stage, password string) (BackupManifest, error) {
	var manifest BackupManifest
	if err := backupPassword(password); err != nil {
		return manifest, err
	}
	file, err := os.Open(input)
	if err != nil {
		return manifest, err
	}
	defer file.Close()
	identity, err := age.NewScryptIdentity(password)
	if err != nil {
		return manifest, err
	}
	identity.SetMaxWorkFactor(18)
	plain, err := age.Decrypt(file, identity)
	if err != nil {
		return manifest, errors.New("backup password is incorrect or backup is damaged")
	}
	archivePath := filepath.Join(stage, "archive.zip")
	archiveFile, err := os.OpenFile(archivePath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return manifest, err
	}
	count, copyErr := io.Copy(archiveFile, io.LimitReader(plain, backupArchiveLimit+1))
	closeErr := archiveFile.Close()
	if copyErr != nil || closeErr != nil {
		return manifest, errors.New("backup authentication or read failed")
	}
	if count > backupArchiveLimit {
		return manifest, errors.New("backup exceeds size limit")
	}
	archive, err := zip.OpenReader(archivePath)
	if err != nil {
		return manifest, errors.New("backup archive is invalid")
	}
	defer archive.Close()
	limits := map[string]uint64{"toolbox.db": backupDatabaseLimit, "vault.key": 32, "manifest.json": 8192}
	seen := map[string]bool{}
	for _, entry := range archive.File {
		if err := ctx.Err(); err != nil {
			return manifest, err
		}
		limit, allowed := limits[entry.Name]
		if !allowed || seen[entry.Name] || !entry.Mode().IsRegular() || entry.UncompressedSize64 > limit {
			return manifest, errors.New("backup has unexpected, repeated or oversized entries")
		}
		seen[entry.Name] = true
		reader, err := entry.Open()
		if err != nil {
			return manifest, err
		}
		output, err := os.OpenFile(filepath.Join(stage, entry.Name), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			reader.Close()
			return manifest, err
		}
		written, copyErr := io.Copy(output, io.LimitReader(reader, int64(limit)+1))
		err = errors.Join(copyErr, reader.Close(), output.Sync(), output.Close())
		if err != nil {
			return manifest, err
		}
		if written > int64(limit) {
			return manifest, errors.New("backup entry exceeds size limit")
		}
	}
	if len(seen) != len(limits) {
		return manifest, errors.New("backup is missing required files")
	}
	payload, err := os.ReadFile(filepath.Join(stage, "manifest.json"))
	if err != nil {
		return manifest, err
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&manifest) != nil || decoder.Decode(new(any)) != io.EOF || manifest.Format != 1 || manifest.Schema < 1 || manifest.Schema > currentSchemaVersion {
		return manifest, errors.New("backup format or schema is unsupported")
	}
	for name, expected := range map[string]string{"toolbox.db": manifest.DatabaseSHA256, "vault.key": manifest.KeySHA256} {
		digest, err := fileDigest(filepath.Join(stage, name))
		if err != nil || digest != expected {
			return manifest, errors.New("backup checksum mismatch")
		}
	}
	return manifest, nil
}

func validateBackupDatabase(ctx context.Context, directory string, manifest BackupManifest) error {
	key, err := os.ReadFile(filepath.Join(directory, "vault.key"))
	if err != nil || len(key) != 32 {
		return errors.New("backup vault key is invalid")
	}
	database, err := sql.Open("sqlite", filepath.Join(directory, "toolbox.db"))
	if err != nil {
		return err
	}
	defer database.Close()
	var schema int
	if err := database.QueryRowContext(ctx, "PRAGMA user_version").Scan(&schema); err != nil || schema != manifest.Schema {
		return errors.New("backup schema does not match manifest")
	}
	var integrity string
	if err := database.QueryRowContext(ctx, "PRAGMA integrity_check(1)").Scan(&integrity); err != nil || integrity != "ok" {
		return errors.New("backup database integrity check failed")
	}
	foreignKeys, err := database.QueryContext(ctx, "PRAGMA foreign_key_check")
	if err != nil {
		return err
	}
	invalid := foreignKeys.Next()
	err = errors.Join(foreignKeys.Err(), foreignKeys.Close())
	if invalid || err != nil {
		return errors.New("backup database has invalid account references")
	}
	instance := &Store{database: database, key: key}
	return instance.verifySecretKey(ctx, database)
}

func (instance *Store) verifySecretKey(ctx context.Context, database interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}) error {
	encryption, err := instance.aead()
	if err != nil {
		return err
	}
	rows, err := database.QueryContext(ctx, "SELECT account_id,purpose,ciphertext FROM secrets")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var account, purpose string
		var ciphertext []byte
		if err := rows.Scan(&account, &purpose, &ciphertext); err != nil {
			return err
		}
		if len(ciphertext) < encryption.NonceSize() {
			return errors.New("database contains an invalid encrypted secret")
		}
		if _, err := encryption.Open(nil, ciphertext[:encryption.NonceSize()], ciphertext[encryption.NonceSize():], []byte(account+"\x00"+purpose)); err != nil {
			return errors.New("database and vault key do not match")
		}
	}
	return rows.Err()
}

func RestoreBackup(ctx context.Context, input, target, password string) error {
	absolute, err := filepath.Abs(target)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(absolute); err == nil {
		return errors.New("restore target must be a new directory")
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	stage, err := os.MkdirTemp(filepath.Dir(absolute), ".toolbox-restore-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	manifest, err := extractBackup(ctx, input, stage, password)
	if err != nil {
		return err
	}
	if err := validateBackupDatabase(ctx, stage, manifest); err != nil {
		return err
	}
	database, err := Open(stage)
	if err != nil {
		return fmt.Errorf("恢复副本初始化或迁移失败，目标目录尚未创建：%w", err)
	}
	err = database.prepareRestoredAccounts(ctx)
	if err := errors.Join(err, database.Close()); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := os.Mkdir(absolute, 0700); err != nil {
		return err
	}
	marker := filepath.Join(absolute, ".restore-in-progress")
	if err := os.WriteFile(marker, []byte("Do not start until restore completes.\n"), 0600); err != nil {
		os.Remove(absolute)
		return err
	}
	for _, name := range []string{"toolbox.db", "vault.key"} {
		if err := os.Rename(filepath.Join(stage, name), filepath.Join(absolute, name)); err != nil {
			return fmt.Errorf("restore not published; incomplete directory remains marked: %w", err)
		}
	}
	return os.Remove(marker)
}

func (instance *Store) prepareRestoredAccounts(ctx context.Context) error {
	accounts, err := instance.Accounts(ctx)
	if err != nil {
		return err
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	for _, account := range accounts {
		if err := pauseAccountDocuments(ctx, transaction, account.Account); err != nil {
			return err
		}
	}
	for _, statement := range []string{
		"UPDATE accounts SET generation=generation+1",
		"DELETE FROM sessions",
		"DELETE FROM site_auth_sessions",
		"DELETE FROM auth_recovery_contexts",
		"DELETE FROM secrets WHERE purpose='campus-service-jwxk'",
		"DELETE FROM site_settings WHERE setting_key='runtime-update-job'",
		`UPDATE documents SET payload=CAST(json_set(payload,'$.state','unknown') AS BLOB),revision=revision+1 WHERE namespace='selection-attempts' AND deleted=0 AND json_extract(payload,'$.state')='pending'`,
		"UPDATE mail_outbox SET state='cancelled',payload=X'',claim='',lease_until=0 WHERE state IN ('queued','sending')",
		`UPDATE documents SET payload=CAST(json_set(payload,'$.enabled',json('false')) AS BLOB),revision=revision+1 WHERE namespace='grade-tracking' AND document_key='config' AND deleted=0`,
	} {
		if _, err := transaction.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	if err := transaction.Commit(); err != nil {
		return err
	}
	_, err = instance.database.ExecContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)")
	return err
}
