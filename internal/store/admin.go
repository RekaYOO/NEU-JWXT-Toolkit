package store

import (
	"context"
	"database/sql"
	"strings"
	"time"
)

type AccountInfo struct {
	Account    string `json:"account"`
	Enabled    bool   `json:"enabled"`
	Generation int64  `json:"generation"`
	Created    string `json:"created_at"`
}

func (instance *Store) Accounts(ctx context.Context) ([]AccountInfo, error) {
	rows, err := instance.database.QueryContext(ctx, `SELECT id,enabled,generation,created_at FROM accounts ORDER BY created_at,id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []AccountInfo{}
	for rows.Next() {
		var item AccountInfo
		if err := rows.Scan(&item.Account, &item.Enabled, &item.Generation, &item.Created); err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (instance *Store) AdmitAccount(ctx context.Context, account string, single bool) error {
	if strings.TrimSpace(account) != account || account == "" || len(account) > 64 {
		return ErrAccess
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	if single {
		var count int
		if err := transaction.QueryRowContext(ctx, `SELECT count(*) FROM accounts WHERE enabled=1 AND id<>?`, account).Scan(&count); err != nil {
			return err
		}
		if count > 0 {
			return ErrConflict
		}
	}
	if _, err := transaction.ExecContext(ctx, `INSERT INTO accounts(id,created_at) VALUES(?,?) ON CONFLICT(id) DO NOTHING`, account, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		return err
	}
	return transaction.Commit()
}

func (instance *Store) SetAdmission(ctx context.Context, account string, enabled, single bool) error {
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	if enabled && single {
		var count int
		if err := transaction.QueryRowContext(ctx, `SELECT count(*) FROM accounts WHERE enabled=1 AND id<>?`, account).Scan(&count); err != nil {
			return err
		}
		if count > 0 {
			return ErrConflict
		}
	}
	result, err := transaction.ExecContext(ctx, `UPDATE accounts SET enabled=?,generation=generation+1 WHERE id=?`, enabled, account)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrMissing
	}
	if err := pauseAccountDocuments(ctx, transaction, account); err != nil {
		return err
	}
	return transaction.Commit()
}

func pauseAccountDocuments(ctx context.Context, transaction *sql.Tx, account string) error {
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := transaction.ExecContext(ctx, `UPDATE documents SET payload=CAST(json_set(payload,'$.desired_state','paused','$.status',
	 CASE WHEN json_extract(payload,'$.status') IN ('running','waiting') THEN 'paused' ELSE json_extract(payload,'$.status') END,
	 '$.message','账号活动已停止，请手动恢复任务','$.updated_at',?) AS BLOB),revision=revision+1,updated_at=?
	 WHERE account_id=? AND namespace='jwxk-automation-tasks' AND deleted=0 AND json_extract(payload,'$.desired_state')='running'`, now, now, account)
	return err
}
