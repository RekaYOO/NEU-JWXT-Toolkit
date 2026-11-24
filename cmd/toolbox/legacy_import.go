package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/application"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/processlock"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func runLegacyImport(ctx context.Context, args []string, output io.Writer) error {
	flags := flag.NewFlagSet("import-legacy", flag.ContinueOnError)
	flags.SetOutput(output)
	data := flags.String("data", "", "existing Go data directory")
	source := flags.String("source", "", "legacy Python data directory, read-only")
	account := flags.String("account", "", "explicit existing enabled destination account")
	digest := flags.String("apply-digest", "", "apply exactly the preview digest; omitted means preview only")
	terms := flags.String("terms", "", "comma-separated explicit agenda term codes")
	sharedOwner := flags.String("shared-settings-owner", "", "import only shared mail/tracking/recovery settings and explicitly assign them to this account")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if *data == "" || *source == "" || *account == "" || flags.NArg() != 0 {
		return errors.New("import-legacy requires --data, --source and --account")
	}
	for _, name := range []string{"toolbox.db", "vault.key"} {
		info, err := os.Lstat(filepath.Join(*data, name))
		if err != nil || !info.Mode().IsRegular() {
			return errors.New("legacy import requires an existing Go data directory")
		}
	}
	lock, err := processlock.Acquire(*data)
	if err != nil {
		return err
	}
	defer lock.Close()
	database, err := store.Open(*data)
	if err != nil {
		return err
	}
	defer database.Close()
	termCodes := []string{}
	if strings.TrimSpace(*terms) != "" {
		for _, term := range strings.Split(*terms, ",") {
			termCodes = append(termCodes, strings.TrimSpace(term))
		}
	}
	var report application.LegacyImportReport
	var importErr error
	if *sharedOwner != "" {
		if len(termCodes) > 0 {
			return errors.New("shared settings import does not accept agenda terms")
		}
		report, importErr = application.ImportLegacySharedSettings(ctx, database, *source, *account, *sharedOwner, *digest)
	} else {
		report, importErr = application.ImportLegacy(ctx, database, *source, *account, *digest, termCodes...)
	}
	encoder := json.NewEncoder(output)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(report); err != nil {
		return err
	}
	return importErr
}
