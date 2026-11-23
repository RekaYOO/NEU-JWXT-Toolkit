package store

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestSitePasswordsRoleIsolationRotationAndSessionRevocation(t *testing.T) {
	directory := t.TempDir()
	database, err := Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, purpose := range []string{"access", "admin"} {
		if err := database.SetSitePassword(ctx, purpose, "test-password-"+purpose, false); err != nil {
			t.Fatal(err)
		}
	}
	if err := database.SetSitePassword(ctx, "access", "another-password", false); !errors.Is(err, ErrConflict) {
		t.Fatal("initialization replaced password", err)
	}
	payload, _ := database.SiteSetting(ctx, "credential-access")
	if strings.Contains(string(payload), "test-password") {
		t.Fatal("plaintext password stored")
	}
	if _, err := database.CreateSiteSession(ctx, "access", "wrong", time.Hour); !errors.Is(err, ErrAccess) {
		t.Fatal(err)
	}
	token, err := database.CreateSiteSession(ctx, "access", "test-password-access", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if !database.SiteSession(ctx, "access", token) || database.SiteSession(ctx, "admin", token) {
		t.Fatal("role boundary failed")
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	database, err = Open(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if !database.SiteSession(ctx, "access", token) {
		t.Fatal("access session lost on restart")
	}
	if err := database.RevokeSiteSession(ctx, "access", token); err != nil {
		t.Fatal(err)
	}
	if database.SiteSession(ctx, "access", token) {
		t.Fatal("logged out token accepted")
	}
	token, err = database.CreateSiteSession(ctx, "access", "test-password-access", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if err := database.SetSitePassword(ctx, "access", "rotated-password", true); err != nil {
		t.Fatal(err)
	}
	if database.SiteSession(ctx, "access", token) {
		t.Fatal("rotated password retained old session")
	}
	if _, err := database.CreateSiteSession(ctx, "access", "test-password-access", time.Hour); !errors.Is(err, ErrAccess) {
		t.Fatal("old password accepted", err)
	}
	if _, err := database.CreateSiteSession(ctx, "admin", "test-password-admin", time.Hour); err != nil {
		t.Fatal("other role changed", err)
	}
}

func TestBrowserLogoutIsAtomicAndDeviceScoped(t *testing.T) {
	database, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	ctx := context.Background()
	var devices [2]map[string]string
	for index := range devices {
		devices[index] = map[string]string{}
	}
	for _, scope := range []string{"access", "admin"} {
		password := "test-password-" + scope
		if err := database.SetSitePassword(ctx, scope, password, false); err != nil {
			t.Fatal(err)
		}
		for _, device := range devices {
			token, err := database.CreateSiteSession(ctx, scope, password, time.Hour)
			if err != nil {
				t.Fatal(err)
			}
			device[scope] = token
		}
	}
	if _, err := database.database.Exec(`CREATE TRIGGER reject_logout BEFORE DELETE ON site_auth_sessions WHEN OLD.purpose='admin' BEGIN SELECT RAISE(ABORT,'fixture'); END`); err != nil {
		t.Fatal(err)
	}
	logout := func() error {
		return database.RevokeBrowserSiteSessions(ctx, devices[0]["access"], devices[0]["admin"])
	}
	verify := func(firstActive bool) {
		t.Helper()
		for index, device := range devices {
			for scope, token := range device {
				if database.SiteSession(ctx, scope, token) != (index == 1 || firstActive) {
					t.Fatalf("unexpected session state: device %d, scope %s", index, scope)
				}
			}
		}
	}
	if logout() == nil {
		t.Fatal("expected failure")
	}
	verify(true)
	if _, err := database.database.Exec(`DROP TRIGGER reject_logout`); err != nil {
		t.Fatal(err)
	}
	if err := logout(); err != nil {
		t.Fatal(err)
	}
	verify(false)
}
