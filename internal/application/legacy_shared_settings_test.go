package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestLegacySharedSettingsExplicitOwnerEncryptedAndPaused(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.database.AddAccount(ctx, "student-a")
	api.database.AddAccount(ctx, "student-b")
	cookie := loginCookie(t, handler, "student-a")
	root := t.TempDir()
	writeLegacyFixture(t, root, "grade_tracking_config.json", `{"enabled":true,"interval_minutes":15,"start_hour":8,"end_hour":22}`)
	writeLegacyFixture(t, root, "system_mail_config.json", `{"smtp_host":"smtp.example.com","smtp_password":"fixture-private-password","from_email":"a@example.com","to_email":"b@example.com"}`)
	writeLegacyFixture(t, root, "auth_recovery_config.json", `{"public_base_url":"https://toolbox.example.com/","link_ttl_hours":6}`)
	if _, err := ImportLegacySharedSettings(ctx, api.database, root, "student-a", "student-b", ""); err == nil {
		t.Fatal("ambiguous owner accepted")
	}
	preview, err := ImportLegacySharedSettings(ctx, api.database, root, "student-a", "student-a", "")
	if err != nil || len(preview.Items) != 4 || preview.Applied != 0 {
		t.Fatal(preview, err)
	}
	printed, _ := json.Marshal(preview)
	if strings.Contains(string(printed), "fixture-private-password") || strings.Contains(string(printed), "smtp.example.com") {
		t.Fatal("preview exposed settings")
	}
	if _, err := api.database.ReadSecret(ctx, "student-a", "mail-config"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("preview wrote secret", err)
	}
	applied, err := ImportLegacySharedSettings(ctx, api.database, root, "student-a", "student-a", preview.Digest)
	if err != nil || applied.Applied != 4 {
		t.Fatal(applied, err)
	}
	repeat, err := ImportLegacySharedSettings(ctx, api.database, root, "student-a", "student-a", preview.Digest)
	if err != nil || repeat.Applied != 0 {
		t.Fatal(repeat, err)
	}
	for _, route := range []struct{ path, contains string }{
		{"/api/grade-tracking/config", `"enabled":false`},
		{"/api/system-settings/mail", `"smtp_password_configured":true`},
		{"/api/system-settings/auth-recovery", `"link_ttl_hours":6`},
	} {
		response := apiRequest(handler, "GET", route.path, "", cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), route.contains) || strings.Contains(response.Body.String(), "fixture-private-password") {
			t.Fatal(route.path, response.Code, response.Body.String())
		}
	}
	if _, err := api.database.ReadSecret(ctx, "student-b", "mail-config"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("cross-account migration", err)
	}
	api.database.DeleteSecret(ctx, "student-a", "mail-config")
	if _, err := ImportLegacySharedSettings(ctx, api.database, root, "student-a", "student-a", preview.Digest); err == nil {
		t.Fatal("deleted secret resurrected")
	}
}

func TestLegacySharedSettingsFallbackAndConflicts(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	ctx := context.Background()
	loginCookie(t, handler, "student")
	root := t.TempDir()
	writeLegacyFixture(t, root, "grade_tracking_config.json", `{"enabled":true,"smtp_host":"smtp.example.com","smtp_password":"private-test","site_url":"https://toolbox.example.com"}`)
	preview, err := ImportLegacySharedSettings(ctx, api.database, root, "student", "student", "")
	if err != nil || len(preview.Items) != 4 {
		t.Fatal(preview, err)
	}
	if err := api.database.SaveSecret(ctx, "student", "mail-config", []byte(`{"smtp_host":"existing.example.com","smtp_port":465,"smtp_security":"ssl"}`)); err != nil {
		t.Fatal(err)
	}
	if _, err := ImportLegacySharedSettings(ctx, api.database, root, "student", "student", preview.Digest); err == nil {
		t.Fatal("existing SMTP replaced")
	}
	if _, err := api.database.Read(ctx, "student", "grade-tracking", "config"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("partial config imported", err)
	}
	writeLegacyFixture(t, root, "grade_tracking_config.json", `{"interval_minutes":1}`)
	if _, err := ImportLegacySharedSettings(ctx, api.database, root, "student", "student", ""); err == nil {
		t.Fatal("invalid schedule accepted")
	}
}
