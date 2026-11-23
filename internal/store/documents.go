package store

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

func (instance *Store) ListDocuments(ctx context.Context, session Session, namespace string) ([]Document, error) {
	if !validDocument(namespace, "list") {
		return nil, errors.New("invalid document namespace")
	}
	rows, err := instance.database.QueryContext(ctx, `SELECT d.document_key,d.payload,d.revision,d.deleted,d.updated_at
	 FROM documents d JOIN accounts a ON a.id=d.account_id
	 WHERE a.id=? AND a.enabled=1 AND a.generation=? AND d.namespace=? AND d.deleted=0 ORDER BY d.updated_at DESC,d.document_key`, session.Account, session.Generation, namespace)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Document{}
	for rows.Next() {
		var document Document
		if err := rows.Scan(&document.Key, &document.Payload, &document.Revision, &document.Deleted, &document.Updated); err != nil {
			return nil, err
		}
		result = append(result, document)
	}
	return result, rows.Err()
}

func (instance *Store) RenameDocument(ctx context.Context, session Session, namespace, source, destination string, expected int64) (Document, error) {
	if !validDocument(namespace, source) || !validDocument(namespace, destination) || expected <= 0 {
		return Document{}, errors.New("invalid document rename")
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return Document{}, err
	}
	defer transaction.Rollback()
	var document Document
	err = transaction.QueryRowContext(ctx, `SELECT d.payload,d.revision FROM documents d JOIN accounts a ON a.id=d.account_id
	 WHERE a.id=? AND a.enabled=1 AND a.generation=? AND d.namespace=? AND d.document_key=? AND d.deleted=0`, session.Account, session.Generation, namespace, source).Scan(&document.Payload, &document.Revision)
	if errors.Is(err, sql.ErrNoRows) {
		return Document{}, ErrMissing
	}
	if err != nil {
		return Document{}, err
	}
	if document.Revision != expected {
		return Document{}, ErrConflict
	}
	if source == destination {
		document.Key = source
		return document, nil
	}
	document.Key, document.Revision, document.Updated = destination, 1, time.Now().UTC().Format(time.RFC3339Nano)
	result, err := transaction.ExecContext(ctx, `INSERT INTO documents(account_id,namespace,document_key,payload,revision,deleted,updated_at)
	 VALUES(?,?,?,?,1,0,?) ON CONFLICT DO NOTHING`, session.Account, namespace, destination, []byte(document.Payload), document.Updated)
	if err != nil {
		return Document{}, err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return Document{}, ErrConflict
	}
	if _, err := transaction.ExecContext(ctx, `UPDATE documents SET payload='null',revision=revision+1,deleted=1,updated_at=?
	 WHERE account_id=? AND namespace=? AND document_key=?`, document.Updated, session.Account, namespace, source); err != nil {
		return Document{}, err
	}
	if err := transaction.Commit(); err != nil {
		return Document{}, err
	}
	return document, nil
}
