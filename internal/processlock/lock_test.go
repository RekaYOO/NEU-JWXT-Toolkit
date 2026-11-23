package processlock

import (
	"errors"
	"testing"
)

func TestDirectoryLockPreventsSecondRuntimeAndReleasesOnClose(t *testing.T) {
	directory := t.TempDir()
	first, err := Acquire(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := Acquire(directory); !errors.Is(err, ErrInUse) {
		if second != nil {
			second.Close()
		}
		t.Fatal("duplicate runtime acquired lock", err)
	}
	other, err := Acquire(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	other.Close()
	first.Close()
	next, err := Acquire(directory)
	if err != nil {
		t.Fatal("closed runtime left stale lock", err)
	}
	next.Close()
}
