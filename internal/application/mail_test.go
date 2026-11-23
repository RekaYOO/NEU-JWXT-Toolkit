package application

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
)

func TestMailSettingsAreEncryptedIsolatedAndNeverReturnPassword(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(context.Background(), account)
	}
	cookieA, cookieB := loginCookie(t, handler, "student-a"), loginCookie(t, handler, "student-b")
	endpoint := "/api/system-settings/mail"
	if response := apiRequest(handler, "GET", endpoint, "", nil); response.Code != 401 {
		t.Fatal(response)
	}
	body := `{"smtp_host":"smtp.example.invalid","smtp_port":465,"smtp_security":"ssl","smtp_username":"sender@example.invalid","smtp_password":"test-only-secret","from_email":"sender@example.invalid","to_email":"recipient@example.invalid"}`
	response := apiRequest(handler, "PUT", endpoint, body, cookieA)
	if response.Code != 200 || strings.Contains(response.Body.String(), "test-only-secret") || !strings.Contains(response.Body.String(), `"smtp_password_configured":true`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "GET", endpoint, "", cookieB)
	if response.Code != 200 || strings.Contains(response.Body.String(), "sender@example.invalid") || !strings.Contains(response.Body.String(), `"smtp_password_configured":false`) {
		t.Fatal(response)
	}
	apiRequest(handler, "PUT", endpoint, `{"smtp_password":""}`, cookieA)
	sent := 0
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		sent++
		if config.Password != "test-only-secret" || config.To != "recipient@example.invalid" || message.Subject == "" {
			t.Fatal("saved credential not preserved")
		}
		return nil
	}
	response = apiRequest(handler, "POST", endpoint+"/test", "", cookieA)
	if response.Code != 200 || sent != 1 {
		t.Fatal(response)
	}
	api.sendMail = func(context.Context, notifications.MailConfig, notifications.MailMessage) error {
		return errors.New("server echoed test-only-secret")
	}
	response = apiRequest(handler, "POST", endpoint+"/test", "", cookieA)
	if response.Code != 502 || strings.Contains(response.Body.String(), "test-only-secret") {
		t.Fatal(response)
	}
	response = apiRequest(handler, "PUT", endpoint, `{"clear_smtp_password":true}`, cookieA)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"smtp_password_configured":false`) {
		t.Fatal(response)
	}
	response = apiRequest(handler, "POST", endpoint+"/test", "", cookieA)
	if response.Code != 400 {
		t.Fatal("incomplete auth configuration sent", response)
	}
	for _, invalid := range []string{`{"smtp_port":0}`, `{"smtp_security":"auto"}`, `{"from_email":"user@example.invalid\r\nBcc: other@example.invalid"}`, `{"smtp_password_configured":true}`} {
		if response := apiRequest(handler, "PUT", endpoint, invalid, cookieA); response.Code != 422 {
			t.Fatal(response)
		}
	}
}
