package store

import "context"

func (instance *Store) SaveThemeDefault(ctx context.Context, previous, next string) error {
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	if _, err := transaction.ExecContext(ctx, `INSERT INTO site_settings(setting_key,value) VALUES('appearance-default',?) ON CONFLICT DO NOTHING`, []byte("blue")); err != nil {
		return err
	}
	result, err := transaction.ExecContext(ctx, `UPDATE site_settings SET value=? WHERE setting_key='appearance-default' AND value=?`, []byte(next), []byte(previous))
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count != 1 {
		return ErrConflict
	}
	return transaction.Commit()
}
