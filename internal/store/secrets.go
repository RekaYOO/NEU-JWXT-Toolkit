package store

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"database/sql"
	"errors"
	"os"
	"path/filepath"
)

func (instance *Store) openKey(directory string, transaction *sql.Tx) error {
	keyPath := filepath.Join(directory, "vault.key")
	key, err := os.ReadFile(keyPath)
	if errors.Is(err, os.ErrNotExist) {
		var count int
		if err := transaction.QueryRow("SELECT count(*) FROM secrets").Scan(&count); err != nil {
			return err
		}
		if count > 0 {
			return errors.New("vault key is missing; restore it together with the database backup")
		}
		key = make([]byte, 32)
		if _, err := rand.Read(key); err != nil {
			return err
		}
		file, err := os.OpenFile(keyPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if errors.Is(err, os.ErrExist) {
			key, err = os.ReadFile(keyPath)
			if err != nil {
				return err
			}
		} else if err != nil {
			return err
		} else {
			_, writeErr := file.Write(key)
			syncErr := file.Sync()
			closeErr := file.Close()
			if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
				return err
			}
		}
	} else if err != nil {
		return err
	}
	if len(key) != 32 {
		return errors.New("vault key is invalid")
	}
	instance.key = key
	return os.Chmod(keyPath, 0600)
}

func (instance *Store) aead() (cipher.AEAD, error) {
	block, err := aes.NewCipher(instance.key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func (instance *Store) SaveSecret(ctx context.Context, account, purpose string, plaintext []byte) error {
	return instance.saveSecret(ctx, account, purpose, plaintext, nil)
}

func (instance *Store) SaveSecretForSession(ctx context.Context, session Session, purpose string, plaintext []byte) error {
	return instance.saveSecret(ctx, session.Account, purpose, plaintext, &session.Generation)
}

func (instance *Store) saveSecret(ctx context.Context, account, purpose string, plaintext []byte, generation *int64) error {
	if purpose != "campus-session" && purpose != "campus-service-jwxk" && purpose != "mail-config" || len(plaintext) > 512*1024 {
		return errors.New("invalid secret")
	}
	ciphertext, err := instance.encryptSecret(account, purpose, plaintext)
	if err != nil {
		return err
	}
	result, err := instance.database.ExecContext(ctx, `INSERT INTO secrets(account_id,purpose,ciphertext)
	 SELECT id,?,? FROM accounts WHERE id=? AND enabled=1 AND (? IS NULL OR generation=?)
	 ON CONFLICT(account_id,purpose) DO UPDATE SET ciphertext=excluded.ciphertext`, purpose, ciphertext, account, generation, generation)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count != 1 {
		return ErrAccess
	}
	return nil
}

func (instance *Store) encryptSecret(account, purpose string, plaintext []byte) ([]byte, error) {
	encryption, err := instance.aead()
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, encryption.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return encryption.Seal(nonce, nonce, plaintext, []byte(account+"\x00"+purpose)), nil
}

func (instance *Store) ReadSecret(ctx context.Context, account, purpose string) ([]byte, error) {
	var ciphertext []byte
	err := instance.database.QueryRowContext(ctx, `SELECT s.ciphertext FROM secrets s JOIN accounts a ON a.id=s.account_id
	 WHERE a.id=? AND a.enabled=1 AND s.purpose=?`, account, purpose).Scan(&ciphertext)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrMissing
	}
	if err != nil {
		return nil, err
	}
	encryption, err := instance.aead()
	if err != nil {
		return nil, err
	}
	if len(ciphertext) < encryption.NonceSize() {
		return nil, errors.New("stored secret is invalid")
	}
	plaintext, err := encryption.Open(nil, ciphertext[:encryption.NonceSize()], ciphertext[encryption.NonceSize():], []byte(account+"\x00"+purpose))
	if err != nil {
		return nil, errors.New("stored secret could not be authenticated")
	}
	return plaintext, nil
}

func (instance *Store) DeleteSecret(ctx context.Context, account, purpose string) error {
	_, err := instance.database.ExecContext(ctx, "DELETE FROM secrets WHERE account_id=? AND purpose=?", account, purpose)
	return err
}
