package store

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

type EvaluationAttempt struct {
	State   string
	Attempt int64
}

func (instance *Store) EvaluationAttempt(ctx context.Context, session Session, key string) (EvaluationAttempt, error) {
	var result EvaluationAttempt
	err := instance.database.QueryRowContext(ctx, `SELECT e.state,e.attempt FROM evaluation_attempts e JOIN accounts a ON a.id=e.account_id
	 WHERE a.id=? AND a.enabled=1 AND a.generation=? AND e.operation_key=?`, session.Account, session.Generation, key).Scan(&result.State, &result.Attempt)
	if errors.Is(err, sql.ErrNoRows) {
		return result, ErrMissing
	}
	return result, err
}

func (instance *Store) BeginEvaluation(ctx context.Context, session Session, key string) (EvaluationAttempt, error) {
	if len(key) != 64 {
		return EvaluationAttempt{}, errors.New("invalid evaluation key")
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return EvaluationAttempt{}, err
	}
	defer transaction.Rollback()
	var generation int64
	if err := transaction.QueryRowContext(ctx, "SELECT generation FROM accounts WHERE id=? AND enabled=1", session.Account).Scan(&generation); err != nil || generation != session.Generation {
		return EvaluationAttempt{}, ErrAccess
	}
	result := EvaluationAttempt{State: "pending", Attempt: 1}
	var previous EvaluationAttempt
	err = transaction.QueryRowContext(ctx, "SELECT state,attempt FROM evaluation_attempts WHERE account_id=? AND operation_key=?", session.Account, key).Scan(&previous.State, &previous.Attempt)
	if err == nil {
		if previous.State != "rejected" && previous.State != "not_sent" {
			return previous, ErrConflict
		}
		result.Attempt = previous.Attempt + 1
	} else if !errors.Is(err, sql.ErrNoRows) {
		return result, err
	}
	_, err = transaction.ExecContext(ctx, `INSERT INTO evaluation_attempts(account_id,operation_key,state,attempt,updated_at) VALUES(?,?,?,?,?)
	 ON CONFLICT(account_id,operation_key) DO UPDATE SET state=excluded.state,attempt=excluded.attempt,updated_at=excluded.updated_at`, session.Account, key, result.State, result.Attempt, time.Now().UTC().Format(time.RFC3339Nano))
	if err != nil {
		return result, err
	}
	return result, transaction.Commit()
}

func (instance *Store) FinishEvaluation(ctx context.Context, session Session, key string, attempt int64, state string) error {
	if state != "confirmed" && state != "rejected" && state != "unknown" && state != "not_sent" {
		return errors.New("invalid evaluation result")
	}
	result, err := instance.database.ExecContext(ctx, `UPDATE evaluation_attempts SET state=?,updated_at=? WHERE account_id=? AND operation_key=? AND attempt=?
	 AND EXISTS(SELECT 1 FROM accounts WHERE id=? AND enabled=1 AND generation=?)`, state, time.Now().UTC().Format(time.RFC3339Nano), session.Account, key, attempt, session.Account, session.Generation)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrConflict
	}
	return nil
}
