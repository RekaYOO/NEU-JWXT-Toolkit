package store

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"time"
)

type DiagnosticEntry struct {
	Timestamp  string         `json:"timestamp"`
	Category   string         `json:"category"`
	Date       string         `json:"date"`
	Level      string         `json:"level"`
	Logger     string         `json:"logger"`
	Message    string         `json:"message"`
	EventType  string         `json:"event_type"`
	EventTitle string         `json:"event_title"`
	Summary    string         `json:"summary"`
	Details    map[string]any `json:"details"`
	Structured bool           `json:"structured"`
}

func (instance *Store) AppendDiagnostic(ctx context.Context, account string, entry DiagnosticEntry) error {
	return instance.appendDiagnostic(ctx, account, 0, entry)
}

func (instance *Store) AppendSessionDiagnostic(ctx context.Context, session Session, entry DiagnosticEntry) error {
	if session.Generation <= 0 {
		return ErrAccess
	}
	return instance.appendDiagnostic(ctx, session.Account, session.Generation, entry)
}

func (instance *Store) appendDiagnostic(ctx context.Context, account string, generation int64, entry DiagnosticEntry) error {
	if entry.Category != "access" && entry.Category != "login" && entry.Category != "error" && entry.Category != "sync" && entry.Category != "system" {
		return errors.New("invalid log category")
	}
	now := time.Now().UTC()
	entry.Timestamp, entry.Date, entry.Logger, entry.Structured = now.Format(time.RFC3339Nano), now.Format("2006-01-02"), "toolbox", true
	payload, err := json.Marshal(entry)
	if err != nil {
		return err
	}
	if len(payload) > 16384 {
		return errors.New("diagnostic too large")
	}
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer transaction.Rollback()
	result, err := transaction.ExecContext(ctx, `INSERT INTO diagnostic_logs(account_id,category,day,timestamp,payload) SELECT ?,?,?,?,? WHERE ?=0 OR EXISTS(SELECT 1 FROM accounts WHERE id=? AND generation=? AND enabled=1)`, account, entry.Category, entry.Date, entry.Timestamp, payload, generation, account, generation)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil {
		return err
	} else if count != 1 {
		return ErrAccess
	}
	if _, err = transaction.ExecContext(ctx, `DELETE FROM diagnostic_logs WHERE id <= (SELECT MAX(id)-200000 FROM diagnostic_logs) OR day < ?`, now.AddDate(0, 0, -29).Format("2006-01-02")); err != nil {
		return err
	}
	return transaction.Commit()
}

type DiagnosticQuery struct {
	Account  string
	All      bool
	Category string
	Date     string
	Since    string
	Level    string
	Search   string
	Limit    int
	Offset   int
	Newest   bool
}

type DiagnosticFile struct {
	Category  string  `json:"category"`
	Date      string  `json:"date"`
	Filename  string  `json:"filename"`
	SizeBytes int64   `json:"size_bytes"`
	SizeMB    float64 `json:"size_mb"`
	Modified  string  `json:"modified_time"`
}

func (instance *Store) WriteDiagnosticFile(ctx context.Context, query DiagnosticQuery, writer io.Writer) error {
	rows, err := instance.database.QueryContext(ctx, `SELECT payload FROM diagnostic_logs WHERE (? OR account_id=?) AND category=? AND day=? ORDER BY id`, query.All, query.Account, query.Category, query.Date)
	if err != nil {
		return err
	}
	defer rows.Close()
	found := false
	for rows.Next() {
		var payload []byte
		if err := rows.Scan(&payload); err != nil {
			return err
		}
		if _, err := writer.Write(append(payload, '\n')); err != nil {
			return err
		}
		found = true
	}
	if err := rows.Err(); err != nil {
		return err
	}
	if !found {
		return ErrMissing
	}
	return nil
}

func (instance *Store) DiagnosticFiles(ctx context.Context, query DiagnosticQuery) ([]DiagnosticFile, error) {
	rows, err := instance.database.QueryContext(ctx, `SELECT category,day,SUM(length(payload)+1),MAX(timestamp) FROM diagnostic_logs WHERE (? OR account_id=?) AND day>=? AND (?='' OR category=?) GROUP BY category,day ORDER BY day DESC,category`, query.All, query.Account, query.Since, query.Category, query.Category)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	files := []DiagnosticFile{}
	for rows.Next() {
		var file DiagnosticFile
		if err = rows.Scan(&file.Category, &file.Date, &file.SizeBytes, &file.Modified); err != nil {
			return nil, err
		}
		file.Filename = file.Category + "_" + file.Date + ".log"
		file.SizeMB = float64(file.SizeBytes) / (1024 * 1024)
		files = append(files, file)
	}
	return files, rows.Err()
}

func (instance *Store) DiagnosticEntries(ctx context.Context, query DiagnosticQuery) ([]DiagnosticEntry, error) {
	if query.Limit < 1 || query.Limit > 200000 || query.Offset < 0 {
		return nil, errors.New("invalid log bounds")
	}
	order := "ASC"
	if query.Newest {
		order = "DESC"
	}
	rows, err := instance.database.QueryContext(ctx, `SELECT payload FROM diagnostic_logs WHERE (? OR account_id=?) AND (?='' OR category=?) AND (?='' OR day=?) AND day>=? AND (?='' OR json_extract(payload,'$.level')=?) AND (?='' OR instr(lower(CAST(payload AS TEXT)),lower(?))>0) ORDER BY id `+order+` LIMIT ? OFFSET ?`, query.All, query.Account, query.Category, query.Category, query.Date, query.Date, query.Since, query.Level, query.Level, query.Search, query.Search, query.Limit, query.Offset)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	entries := []DiagnosticEntry{}
	for rows.Next() {
		var payload []byte
		var entry DiagnosticEntry
		if err = rows.Scan(&payload); err != nil {
			return nil, err
		}
		if err = json.Unmarshal(payload, &entry); err != nil {
			return nil, err
		}
		entries = append(entries, entry)
	}
	return entries, rows.Err()
}

func (instance *Store) CleanupDiagnostics(ctx context.Context, account string, all bool, before string) (int, error) {
	transaction, err := instance.database.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer transaction.Rollback()
	var count int
	if err = transaction.QueryRowContext(ctx, `SELECT COUNT(*) FROM (SELECT category,day FROM diagnostic_logs WHERE (? OR account_id=?) AND day<? GROUP BY category,day)`, all, account, before).Scan(&count); err != nil {
		return 0, err
	}
	if _, err = transaction.ExecContext(ctx, `DELETE FROM diagnostic_logs WHERE (? OR account_id=?) AND day<?`, all, account, before); err != nil {
		return 0, err
	}
	return count, transaction.Commit()
}
