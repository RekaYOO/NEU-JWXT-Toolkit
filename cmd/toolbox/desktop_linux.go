package main

import (
	"context"
	"errors"
)

func openDesktopBrowser(string) error {
	return errors.New("automatic browser opening is available only on Windows")
}

func waitDesktop(ctx context.Context, finished <-chan error, stop context.CancelFunc, endpoint, icon string, tray, browser bool) error {
	if tray || browser {
		return errors.New("desktop tray and browser options are available only on Windows")
	}
	select {
	case err := <-finished:
		return err
	case <-ctx.Done():
		return nil
	}
}
