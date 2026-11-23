package store

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

func TestDocumentAndMailAtomicCommit(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	database.AddAccount(ctx, "student")
	generation, _ := database.Generation(ctx, "student")
	session := Session{Account: "student", Generation: generation}
	draft := &MailDraft{Source: "grade_tracking", Dedupe: "initial", Payload: json.RawMessage(`{"subject":"test","body":"test"}`)}
	changes := []DocumentChange{{Namespace: "grade-tracking", Key: "state", Payload: json.RawMessage(`{}`)}, {Namespace: "grade-tracking", Key: "snapshot", Payload: json.RawMessage(`{}`), Revision: 1}}
	if _, err := database.SaveBatchWithMail(ctx, session, changes, draft); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if _, err := database.Read(ctx, "student", "grade-tracking", "state"); !errors.Is(err, ErrMissing) {
		t.Fatal("partial document persisted", err)
	}
	if count, _ := database.PendingMailCount(ctx, session, ""); count != 0 {
		t.Fatal(count)
	}
	changes[1].Revision = 0
	if _, err := database.SaveBatchWithMail(ctx, session, changes, draft); err != nil {
		t.Fatal(err)
	}
	if count, _ := database.PendingMailCount(ctx, session, ""); count != 1 {
		t.Fatal(count)
	}
}

func TestMailReceiptMigrationPreservesPendingQueue(t *testing.T) {
	directory := t.TempDir()
	database, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	database.AddAccount(ctx, "student")
	generation, _ := database.Generation(ctx, "student")
	session := Session{Account: "student", Generation: generation}
	if _, err := database.QueueMail(ctx, session, "grade_tracking", "initial", json.RawMessage(`{"subject":"test","body":"test"}`)); err != nil {
		t.Fatal(err)
	}
	if _, err := database.database.Exec(`ALTER TABLE mail_outbox DROP COLUMN sent_at`); err != nil {
		t.Fatal(err)
	}
	if _, err := database.database.Exec(`PRAGMA user_version=7`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	database, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if count, _ := database.PendingMailCount(ctx, session, "grade_tracking"); count != 1 {
		t.Fatal(count)
	}
	if last, err := database.LastMailDelivery(ctx, session, "grade_tracking"); err != nil || last != "" {
		t.Fatal(last, err)
	}
	entry, err := database.ClaimMail(ctx, session, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Truncate(time.Millisecond)
	if err := database.FinishMail(ctx, entry, true, "", now); err != nil {
		t.Fatal(err)
	}
	if last, err := database.LastMailDelivery(ctx, session, "grade_tracking"); err != nil || last != now.UTC().Format(time.RFC3339Nano) {
		t.Fatal(last, err)
	}
}

func TestMailQueueDeduplicationLeaseRetryAndRevocation(t *testing.T) {
	directory := t.TempDir()
	database, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, account := range []string{"a", "b"} {
		database.AddAccount(ctx, account)
	}
	generation, _ := database.Generation(ctx, "a")
	session := Session{Account: "a", Generation: generation}
	payload := []byte(`{"subject":"test","body":"private body"}`)
	queued, err := database.QueueMail(ctx, session, "course_selection", "event", payload)
	if err != nil || !queued {
		t.Fatal(queued, err)
	}
	queued, err = database.QueueMail(ctx, session, "course_selection", "event", payload)
	if err != nil || queued {
		t.Fatal("duplicate accepted", queued, err)
	}
	database.QueueMail(ctx, session, "grade_tracking", "event", payload)
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	database, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if count, _ := database.PendingMailCount(ctx, session, ""); count != 2 {
		t.Fatal("queue lost across reopen", count)
	}
	if count, _ := database.PendingMailCount(ctx, Session{Account: "b", Generation: 1}, ""); count != 0 {
		t.Fatal("queue leaked across accounts")
	}
	database.DiscardMail(ctx, session, "grade_tracking", "")
	now := time.Now()
	first, err := database.ClaimMail(ctx, session, now)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.ClaimMail(ctx, session, now); !errors.Is(err, ErrMissing) {
		t.Fatal("same message claimed twice", err)
	}
	second, err := database.ClaimMail(ctx, session, now.Add(61*time.Second))
	if err != nil || second.ID != first.ID || second.Attempts != 2 || second.Claim == first.Claim {
		t.Fatal("expired lease not recovered", second, err)
	}
	if err := database.FinishMail(ctx, first, true, "", now); !errors.Is(err, ErrConflict) {
		t.Fatal("stale worker finalized message", err)
	}
	if err := database.FinishMail(ctx, second, false, "raw password must not persist", now); err != nil {
		t.Fatal(err)
	}
	if _, err := database.ClaimMail(ctx, session, now.Add(time.Minute)); !errors.Is(err, ErrMissing) {
		t.Fatal("retry ignored backoff", err)
	}
	third, err := database.ClaimMail(ctx, session, now.Add(2*time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if err := database.FinishMail(ctx, third, true, "", now); err != nil {
		t.Fatal(err)
	}
	queued, err = database.QueueMail(ctx, session, "course_selection", "event", payload)
	if err != nil || queued {
		t.Fatal("delivered event queued again", queued, err)
	}
	var saved string
	database.database.QueryRowContext(ctx, "SELECT payload FROM mail_outbox WHERE id=?", third.ID).Scan(&saved)
	if saved != "{}" {
		t.Fatal("delivered personal body retained")
	}
	database.QueueMail(ctx, session, "course_selection", "new-event", payload)
	database.RevokeAccount(ctx, "a")
	sessions, err := database.DueMailAccounts(ctx, now.Add(time.Hour))
	if err != nil || len(sessions) != 0 {
		t.Fatal("revoked queue remained deliverable", sessions, err)
	}
	if _, err := database.QueueMail(ctx, session, "course_selection", "revoked", payload); !errors.Is(err, ErrAccess) {
		t.Fatal(err)
	}
}

func TestMailEventCheckpointAndQueueCommitTogether(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	database.AddAccount(ctx, "a")
	generation, _ := database.Generation(ctx, "a")
	session := Session{Account: "a", Generation: generation}
	if _, err := database.ObserveMailEvent(ctx, session, "source", "task/auth", "failed", []byte(`[]`)); err == nil {
		t.Fatal("invalid payload accepted")
	}
	queued, err := database.ObserveMailEvent(ctx, session, "source", "task/auth", "failed", []byte(`{"subject":"alert"}`))
	if err != nil || !queued {
		t.Fatal("failed transaction consumed event", queued, err)
	}
	queued, err = database.ObserveMailEvent(ctx, session, "source", "task/auth", "failed", []byte(`{"subject":"alert"}`))
	if err != nil || queued {
		t.Fatal("same event repeated", queued, err)
	}
	if current, _ := database.MailEventCurrent(ctx, session, "source", "task/auth", 1); !current {
		t.Fatal("initial checkpoint missing")
	}
	if queued, err := database.ObserveMailEvent(ctx, session, "source", "task/auth", "healthy", nil); err != nil || queued {
		t.Fatal(queued, err)
	}
	if current, _ := database.MailEventCurrent(ctx, session, "source", "task/auth", 1); current {
		t.Fatal("old alert remained current")
	}
	if queued, err := database.ObserveMailEvent(ctx, session, "source", "task/auth", "failed", []byte(`{"subject":"alert again"}`)); err != nil || !queued {
		t.Fatal("new failure episode suppressed", queued, err)
	}
}
