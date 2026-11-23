package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func runDataCommand(ctx context.Context, command string, args []string, output io.Writer) error {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(output)
	data := flags.String("data", "", "backup source or new restore destination directory")
	archive := flags.String("file", "", "encrypted backup file path")
	flags.Usage = func() {
		fmt.Fprintf(output, "Usage: toolbox %s --data DIRECTORY --file BACKUP.age\nSet NEU_JWXT_BACKUP_PASSWORD to a password of at least 12 bytes.\n", command)
		flags.PrintDefaults()
	}
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if *data == "" || *archive == "" || flags.NArg() != 0 {
		return errors.New("backup and restore require explicit --data and --file paths")
	}
	password := os.Getenv("NEU_JWXT_BACKUP_PASSWORD")
	if len(password) < 12 {
		return errors.New("set NEU_JWXT_BACKUP_PASSWORD to at least 12 bytes; do not pass passwords on the command line")
	}
	switch command {
	case "backup":
		for _, name := range []string{"toolbox.db", "vault.key"} {
			info, err := os.Lstat(filepath.Join(*data, name))
			if err != nil || !info.Mode().IsRegular() {
				return errors.New("backup source must contain an existing database and vault key")
			}
		}
		if err := store.BackupDirectory(ctx, *data, *archive, password); err != nil {
			return err
		}
		fmt.Fprintln(output, "Encrypted backup completed. Keep its password separately.")
		return nil
	case "restore":
		if err := store.RestoreBackup(ctx, *archive, *data, password); err != nil {
			return err
		}
		fmt.Fprintln(output, "Restore completed in the new directory. Previous sessions are revoked; tasks and queued notifications are paused. Sign in again and explicitly resume desired tasks.")
		return nil
	default:
		return errors.New("unknown data command")
	}
}
