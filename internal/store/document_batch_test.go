package store

import (
	"context"
	"errors"
	"testing"
)

func TestDocumentBatchConflictRollsBackAllChanges(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	database.AddAccount(ctx, "student-a")
	generation, _ := database.Generation(ctx, "student-a")
	session := Session{Account: "student-a", Generation: generation}
	database.SaveForSession(ctx, session, "settings", "existing", []byte(`{"old":true}`), 0, false)
	_, err = database.SaveBatchForSession(ctx, session, []DocumentChange{{Namespace: "settings", Key: "new", Payload: []byte(`{}`)}, {Namespace: "settings", Key: "existing", Payload: []byte(`{}`), Revision: 3}})
	if !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if _, err := database.Read(ctx, session.Account, "settings", "new"); !errors.Is(err, ErrMissing) {
		t.Fatal("partial insert survived", err)
	}
	document, _ := database.Read(ctx, session.Account, "settings", "existing")
	if document.Revision != 1 || string(document.Payload) != `{"old":true}` {
		t.Fatal(document)
	}
	documents, err := database.SaveBatchForSession(ctx, session, []DocumentChange{{Namespace: "settings", Key: "new", Payload: []byte(`{}`)}, {Namespace: "settings", Key: "existing", Payload: []byte(`{}`), Revision: 1, Deleted: true}})
	if err != nil || len(documents) != 2 || documents[0].Revision != 1 || !documents[1].Deleted || documents[1].Revision != 2 {
		t.Fatal(documents, err)
	}
	_, err = database.SaveBatchForSession(ctx, session, []DocumentChange{{Namespace: "settings", Key: "duplicate", Payload: []byte(`{}`)}, {Namespace: "settings", Key: "duplicate", Payload: []byte(`{}`)}})
	if err == nil {
		t.Fatal("duplicate document in batch accepted")
	}
	database.RevokeAccount(ctx, session.Account)
	if _, err := database.SaveBatchForSession(ctx, session, []DocumentChange{{Namespace: "settings", Key: "revoked", Payload: []byte(`{}`)}}); !errors.Is(err, ErrAccess) {
		t.Fatal(err)
	}
}
