package store

import (
	"context"
	"encoding/json"
	"errors"
)

func (instance *Store) SaveUpdateJob(ctx context.Context, previous, next []byte) error {
	if len(next) > 64*1024 || !json.Valid(next) {
		return errors.New("invalid update job")
	}
	if previous == nil {
		result, err := instance.database.ExecContext(ctx, `INSERT INTO site_settings(setting_key,value) VALUES('runtime-update-job',?) ON CONFLICT DO NOTHING`, next)
		if err != nil {
			return err
		}
		if count, err := result.RowsAffected(); err != nil || count != 1 {
			return ErrConflict
		}
		return nil
	}
	result, err := instance.database.ExecContext(ctx, `UPDATE site_settings SET value=? WHERE setting_key='runtime-update-job' AND value=?`, next, previous)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil || count != 1 {
		return ErrConflict
	}
	return nil
}
