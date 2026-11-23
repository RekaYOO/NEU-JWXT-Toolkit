package processlock

import (
	"errors"
	"os"
	"path/filepath"
)

var ErrInUse = errors.New("data directory is already in use by another toolbox process")

func Acquire(directory string) (*os.File, error) {
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(directory, "runtime.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err := lock(file); err != nil {
		file.Close()
		return nil, err
	}
	return file, nil
}
