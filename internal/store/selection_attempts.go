package store

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
)

type SelectionAttempt struct {
	State    string `json:"state"`
	Number   int64  `json:"attempt"`
	Revision int64  `json:"-"`
	Course   string `json:"course_code,omitempty"`
	Term     string `json:"term_code,omitempty"`
	Batch    string `json:"batch_code,omitempty"`
	Class    string `json:"class_id,omitempty"`
}

func (instance *Store) SelectionAttempt(ctx context.Context, session Session, key string) (SelectionAttempt, error) {
	generation, err := instance.Generation(ctx, session.Account)
	if err != nil {
		return SelectionAttempt{}, err
	}
	if generation != session.Generation {
		return SelectionAttempt{}, ErrAccess
	}
	document, err := instance.Read(ctx, session.Account, "selection-attempts", key)
	if err != nil {
		return SelectionAttempt{}, err
	}
	var attempt SelectionAttempt
	if document.Deleted || json.Unmarshal(document.Payload, &attempt) != nil {
		return attempt, ErrConflict
	}
	attempt.Revision = document.Revision
	return attempt, nil
}

func (instance *Store) BeginSelection(ctx context.Context, session Session, key string) (SelectionAttempt, error) {
	return instance.BeginSelectionOperation(ctx, session, key, "", "", "", "")
}

func (instance *Store) BeginSelectionOperation(ctx context.Context, session Session, key, course, term, batch, class string) (SelectionAttempt, error) {
	decoded, err := hex.DecodeString(key)
	if err != nil || len(decoded) != 32 {
		return SelectionAttempt{}, errors.New("invalid selection operation key")
	}
	previous, err := instance.SelectionAttempt(ctx, session, key)
	if err != nil && !errors.Is(err, ErrMissing) {
		return previous, err
	}
	if err == nil && previous.State != "rejected" && previous.State != "not_sent" {
		return previous, ErrConflict
	}
	attempt := SelectionAttempt{State: "pending", Number: previous.Number + 1, Course: course, Term: term, Batch: batch, Class: class}
	encoded, _ := json.Marshal(attempt)
	document, err := instance.SaveForSession(ctx, session, "selection-attempts", key, encoded, previous.Revision, false)
	attempt.Revision = document.Revision
	return attempt, err
}

func (instance *Store) FinishSelection(ctx context.Context, session Session, key string, attempt SelectionAttempt, state string) error {
	if state != "confirmed" && state != "unknown" && state != "rejected" && state != "not_sent" {
		return errors.New("invalid selection result")
	}
	attempt.State = state
	encoded, _ := json.Marshal(attempt)
	_, err := instance.SaveForSession(ctx, session, "selection-attempts", key, encoded, attempt.Revision, false)
	return err
}
