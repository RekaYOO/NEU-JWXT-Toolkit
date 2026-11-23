package processlock

import (
	"errors"
	"golang.org/x/sys/unix"
	"os"
)

func lock(file *os.File) error {
	err := unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB)
	if errors.Is(err, unix.EWOULDBLOCK) {
		return ErrInUse
	}
	return err
}
