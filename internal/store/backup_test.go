package store

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"filippo.io/age"
)

func TestBackupDirectoryLeavesSourceVersionUnchanged(t *testing.T) {
	root := t.TempDir()
	directory := filepath.Join(root, "source #1")
	source, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	if _, err := source.database.Exec("ALTER TABLE cache_events DROP COLUMN changes; ALTER TABLE mail_outbox DROP COLUMN sent_at; PRAGMA user_version=1"); err != nil {
		t.Fatal(err)
	}
	if err := BackupDirectory(context.Background(), directory, filepath.Join(root, "backup.age"), "synthetic-password"); err != nil {
		t.Fatal(err)
	}
	if err := CheckUpgrade(context.Background(), directory); err != nil {
		t.Fatal(err)
	}
	snapshot, err := CreateUpgradeSnapshot(context.Background(), directory, root)
	if err != nil {
		t.Fatal(err)
	}
	before, err := openBackupReadOnly(filepath.Join(snapshot, "toolbox.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer before.Close()
	var savedVersion int
	if err := before.QueryRow("PRAGMA user_version").Scan(&savedVersion); err != nil || savedVersion != 1 {
		t.Fatal("rollback snapshot was migrated", savedVersion, err)
	}
	var version int
	if err := source.database.QueryRow("PRAGMA user_version").Scan(&version); err != nil || version != 1 {
		t.Fatal(version, err)
	}
	destination := filepath.Join(root, "restored")
	if err := RestoreBackup(context.Background(), filepath.Join(root, "backup.age"), destination, "synthetic-password"); err != nil {
		t.Fatal(err)
	}
	restored, err := openBackupReadOnly(filepath.Join(destination, "toolbox.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer restored.Close()
	if err := restored.QueryRow("PRAGMA user_version").Scan(&version); err != nil || version != currentSchemaVersion {
		t.Fatal(version, err)
	}
	for _, query := range []string{"SELECT changes FROM cache_events LIMIT 0", "SELECT sent_at FROM mail_outbox LIMIT 0"} {
		if _, err := restored.Exec(query); err != nil {
			t.Fatal(err)
		}
		if _, err := source.database.Exec(query); err == nil {
			t.Fatal("source was migrated", query)
		}
	}
}

func TestRestoreMigrationFailureDoesNotPublishTarget(t *testing.T) {
	root := t.TempDir()
	source, err := Open(filepath.Join(root, "source"))
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	if _, err := source.database.Exec("PRAGMA user_version=1"); err != nil {
		t.Fatal(err)
	}
	if err := CheckUpgrade(context.Background(), filepath.Join(root, "source")); err == nil {
		t.Fatal("upgrade check accepted conflicting migration")
	}
	var unchanged int
	if err := source.database.QueryRow("PRAGMA user_version").Scan(&unchanged); err != nil || unchanged != 1 {
		t.Fatal("upgrade check modified source", unchanged, err)
	}
	archive := filepath.Join(root, "backup.age")
	ctx := context.Background()
	if err := source.Backup(ctx, archive, "synthetic-password"); err != nil {
		t.Fatal(err)
	}
	before, err := fileDigest(archive)
	if err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "restored")
	if err := RestoreBackup(ctx, archive, target, "synthetic-password"); err == nil || !strings.Contains(err.Error(), "恢复副本初始化或迁移失败") {
		t.Fatal("expected migration error", err)
	}
	if _, err := os.Stat(target); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("restore target published", err)
	}
	if after, err := fileDigest(archive); err != nil || after != before {
		t.Fatal("input archive changed", err)
	}
}

func TestEncryptedBackupRestoresAccountsAndPausesActivity(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	source, err := Open(filepath.Join(root, "source"))
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	for _, account := range []string{"student-a", "student-b"} {
		if err := source.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
		if _, err := source.Save(ctx, account, "gpa", "plan", json.RawMessage(`{"name":"`+account+` private plan"}`), 0, false); err != nil {
			t.Fatal(err)
		}
	}
	if err := source.InitializeSetting(ctx, "deployment-mode", []byte("remote-multi")); err != nil {
		t.Fatal(err)
	}
	if _, err := source.database.Exec(`INSERT INTO site_settings(setting_key,value) VALUES('runtime-update-job',?)`, []byte(`{"state":"downloaded"}`)); err != nil {
		t.Fatal(err)
	}
	if err := source.SaveSecret(ctx, "student-a", "campus-service-jwxk", []byte("synthetic gateway session")); err != nil {
		t.Fatal(err)
	}
	if err := source.SaveSecret(ctx, "student-a", "campus-session", []byte("synthetic confidential credential")); err != nil {
		t.Fatal(err)
	}
	if _, err := source.Save(ctx, "student-a", "gpa", "deleted", json.RawMessage(`null`), 0, true); err != nil {
		t.Fatal(err)
	}
	if _, err := source.Save(ctx, "student-a", "jwxk-automation-tasks", "task", json.RawMessage(`{"desired_state":"running","status":"running"}`), 0, false); err != nil {
		t.Fatal(err)
	}
	token, err := source.CreateSession(ctx, "student-a", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	session, _ := source.Session(ctx, token)
	operation := strings.Repeat("ab", 32)
	inFlight := strings.Repeat("cd", 32)
	if _, err := source.BeginSelectionOperation(ctx, session, inFlight, "other-course", "term", "round", "class"); err != nil {
		t.Fatal(err)
	}
	attempt, err := source.BeginSelectionOperation(ctx, session, operation, "course", "term", "round", "class")
	if err != nil {
		t.Fatal(err)
	}
	if err := source.FinishSelection(ctx, session, operation, attempt, "unknown"); err != nil {
		t.Fatal(err)
	}
	if _, err := source.SaveBatchWithMail(ctx, session, []DocumentChange{{Namespace: "grade-tracking", Key: "config", Payload: json.RawMessage(`{"enabled":true}`)}}, &MailDraft{Source: "grade-tracking", Dedupe: "test", Payload: json.RawMessage(`{"body":"test"}`)}); err != nil {
		t.Fatal(err)
	}
	if _, err := source.CommitCache(ctx, session, CacheEntry{Resource: "scores", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"scores":[]}`), Revision: "scores-original"}, "test", nil); err != nil {
		t.Fatal(err)
	}
	backup := filepath.Join(root, "snapshot.age")
	password := "synthetic-backup-password"
	if err := source.Backup(ctx, backup, password); err != nil {
		t.Fatal(err)
	}
	sealed, err := os.ReadFile(backup)
	if err != nil || !bytes.HasPrefix(sealed, []byte("age-encryption.org/v1")) || bytes.Contains(sealed, []byte("private plan")) || bytes.Contains(sealed, []byte("synthetic confidential")) {
		t.Fatal("backup not encrypted", err)
	}
	if _, err := source.Session(ctx, token); err != nil {
		t.Fatal("backup changed active session", err)
	}
	if _, err := source.Save(ctx, "student-a", "gpa", "later", json.RawMessage(`{}`), 0, false); err != nil {
		t.Fatal(err)
	}
	if err := source.Backup(ctx, backup, password); err == nil {
		t.Fatal("overwrote existing backup")
	}
	for _, item := range []struct {
		name, password string
		corrupt        bool
	}{{"wrong", "wrong-backup-password", false}, {"damaged", password, true}} {
		input := backup
		if item.corrupt {
			input = filepath.Join(root, "damaged.age")
			if err := os.WriteFile(input, sealed[:len(sealed)-10], 0600); err != nil {
				t.Fatal(err)
			}
		}
		target := filepath.Join(root, item.name)
		if err := RestoreBackup(ctx, input, target, item.password); err == nil {
			t.Fatal("invalid backup accepted")
		}
		if _, err := os.Stat(target); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("failed restore modified destination")
		}
		staging, err := filepath.Glob(filepath.Join(root, ".toolbox-restore-*"))
		if err != nil || len(staging) != 0 {
			t.Fatalf("%s restore left staging data: %v (%v)", item.name, staging, err)
		}
	}
	target := filepath.Join(root, "restored")
	if err := RestoreBackup(ctx, backup, target, password); err != nil {
		t.Fatal(err)
	}
	if err := RestoreBackup(ctx, backup, target, password); err == nil {
		t.Fatal("overwrote existing data")
	}
	restored, err := Open(target)
	if err != nil {
		t.Fatal(err)
	}
	defer restored.Close()
	for _, account := range []string{"student-a", "student-b"} {
		document, err := restored.Read(ctx, account, "gpa", "plan")
		if err != nil || !bytes.Contains(document.Payload, []byte(account)) {
			t.Fatal("lost account document", err)
		}
	}
	if _, err := restored.Read(ctx, "student-a", "gpa", "later"); err != ErrMissing {
		t.Fatal("snapshot included later write", err)
	}
	deleted, err := restored.Read(ctx, "student-a", "gpa", "deleted")
	if err != nil || !deleted.Deleted {
		t.Fatal("lost deletion tombstone")
	}
	restoredGeneration, err := restored.Generation(ctx, session.Account)
	if err != nil {
		t.Fatal(err)
	}
	current := Session{Account: session.Account, Generation: restoredGeneration}
	interrupted, err := restored.BeginSelection(ctx, current, inFlight)
	if !errors.Is(err, ErrConflict) || interrupted.State != "unknown" || interrupted.Number != 1 || interrupted.Course != "other-course" {
		t.Fatal("interrupted submission not preserved for reconciliation", interrupted, err)
	}
	if original, err := source.SelectionAttempt(ctx, session, inFlight); err != nil || original.State != "pending" {
		t.Fatal("original submission changed", original, err)
	}
	unknown, err := restored.BeginSelectionOperation(ctx, current, operation, "course", "term", "round", "class")
	if !errors.Is(err, ErrConflict) || unknown.State != "unknown" || unknown.Number != 1 || unknown.Course != "course" || unknown.Term != "term" || unknown.Batch != "round" || unknown.Class != "class" {
		t.Fatal("lost unknown operation or allowed duplicate", unknown, err)
	}
	secret, err := restored.ReadSecret(ctx, "student-a", "campus-session")
	if err != nil || string(secret) != "synthetic confidential credential" {
		t.Fatal("lost matching key", err)
	}
	if _, err := restored.Session(ctx, token); err != ErrAccess {
		t.Fatal("old session restored")
	}
	if candidates, err := restored.RestorableAccounts(ctx); err != nil || len(candidates) != 0 {
		t.Fatal("would resume campus sessions", err)
	}
	if candidates, err := restored.ServiceSessionAccounts(ctx); err != nil || len(candidates) != 0 {
		t.Fatal("would restore independent service session", err)
	}
	if _, err := restored.SiteSetting(ctx, "runtime-update-job"); !errors.Is(err, ErrMissing) {
		t.Fatal("restored stale update task", err)
	}
	if value, err := source.SiteSetting(ctx, "runtime-update-job"); err != nil || string(value) != `{"state":"downloaded"}` {
		t.Fatal("source update task changed", err)
	}
	if _, err := source.ReadSecret(ctx, "student-a", "campus-service-jwxk"); err != nil {
		t.Fatal("source service session changed", err)
	}
	task, _ := restored.Read(ctx, "student-a", "jwxk-automation-tasks", "task")
	if !bytes.Contains(task.Payload, []byte(`"desired_state":"paused"`)) {
		t.Fatal("task was not paused", string(task.Payload))
	}
	config, _ := restored.Read(ctx, "student-a", "grade-tracking", "config")
	if !bytes.Contains(config.Payload, []byte(`"enabled":false`)) {
		t.Fatal("tracking was not paused", string(config.Payload))
	}
	var pending int
	if err := restored.database.QueryRow("SELECT count(*) FROM mail_outbox WHERE state IN ('queued','sending')").Scan(&pending); err != nil || pending != 0 {
		t.Fatal("mail would resume", err)
	}
	generation, _ := restored.Generation(ctx, "student-a")
	entry, err := restored.ReadCache(ctx, Session{Account: "student-a", Generation: generation}, "scores", "default")
	if err != nil || entry.Revision != "scores-original" {
		t.Fatal("cache lost", err)
	}
	mode, _ := restored.SiteSetting(ctx, "deployment-mode")
	if string(mode) != "remote-multi" {
		t.Fatal("deployment mode lost")
	}
}

func writeBackupFixture(t *testing.T, path string, entries []struct {
	name string
	data []byte
}) {
	t.Helper()
	recipient, err := age.NewScryptRecipient("synthetic-backup-password")
	if err != nil {
		t.Fatal(err)
	}
	recipient.SetWorkFactor(10)
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	encrypted, err := age.Encrypt(file, recipient)
	if err != nil {
		t.Fatal(err)
	}
	archive := zip.NewWriter(encrypted)
	for _, entry := range entries {
		writer, err := archive.Create(entry.name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write(entry.data); err != nil {
			t.Fatal(err)
		}
	}
	if err := errors.Join(archive.Close(), encrypted.Close(), file.Close()); err != nil {
		t.Fatal(err)
	}
}

func TestBackupRejectsUnexpectedFilesAndMismatchedKey(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	for _, name := range []string{"../outside", "toolbox.db-wal", "vault.key/child"} {
		archive := filepath.Join(root, "invalid.age")
		writeBackupFixture(t, archive, []struct {
			name string
			data []byte
		}{{name, []byte("invalid")}})
		if err := RestoreBackup(ctx, archive, filepath.Join(root, "invalid-target"), "synthetic-backup-password"); err == nil {
			t.Fatal("unexpected archive accepted", name)
		}
	}
	source, err := Open(filepath.Join(root, "source"))
	if err != nil {
		t.Fatal(err)
	}
	if err := source.AddAccount(ctx, "student"); err != nil {
		t.Fatal(err)
	}
	if err := source.SaveSecret(ctx, "student", "campus-session", []byte("synthetic")); err != nil {
		t.Fatal(err)
	}
	if err := source.Close(); err != nil {
		t.Fatal(err)
	}
	database, err := os.ReadFile(filepath.Join(root, "source", "toolbox.db"))
	if err != nil {
		t.Fatal(err)
	}
	wrongKey := bytes.Repeat([]byte{9}, 32)
	databaseHash, keyHash := sha256.Sum256(database), sha256.Sum256(wrongKey)
	manifest, _ := json.Marshal(BackupManifest{Format: 1, Schema: 10, DatabaseSHA256: hex.EncodeToString(databaseHash[:]), KeySHA256: hex.EncodeToString(keyHash[:])})
	archive := filepath.Join(root, "mismatched.age")
	writeBackupFixture(t, archive, []struct {
		name string
		data []byte
	}{{"toolbox.db", database}, {"vault.key", wrongKey}, {"manifest.json", manifest}})
	if err := RestoreBackup(ctx, archive, filepath.Join(root, "mismatched-target"), "synthetic-backup-password"); err == nil {
		t.Fatal("key mismatch accepted")
	}
	marked := filepath.Join(root, "incomplete")
	os.Mkdir(marked, 0700)
	os.WriteFile(filepath.Join(marked, ".restore-in-progress"), []byte("incomplete"), 0600)
	if database, err := Open(marked); err == nil {
		database.Close()
		t.Fatal("started incomplete restore")
	}
}
