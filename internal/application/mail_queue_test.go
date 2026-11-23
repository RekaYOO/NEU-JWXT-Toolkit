package application

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func setupMailQueue(t *testing.T) (*API, store.Session) {
	t.Helper()
	api, handler := testApplication(t, "remote-multi")
	api.accounts.mutex.Lock()
	api.accounts.mailTick = nil
	api.accounts.mutex.Unlock()
	api.database.AddAccount(context.Background(), "student-a")
	loginCookie(t, handler, "student-a")
	generation, _ := api.database.Generation(context.Background(), "student-a")
	session := store.Session{Account: "student-a", Generation: generation}
	config := notifications.MailConfig{Host: "smtp.example.invalid", Port: 465, Security: "ssl", From: "sender@example.invalid", To: "recipient@example.invalid"}
	payload, _ := json.Marshal(config)
	if err := api.database.SaveSecretForSession(context.Background(), session, "mail-config", payload); err != nil {
		t.Fatal(err)
	}
	return api, session
}

func TestMailQueueDeliversOnceAndDoesNotUseSchoolRequests(t *testing.T) {
	api, session := setupMailQueue(t)
	ctx := context.Background()
	message := notifications.MailMessage{Subject: "test", Body: "test body"}
	queued, err := api.queueMail(ctx, session, "test", "event", message)
	if err != nil || !queued {
		t.Fatal(queued, err)
	}
	sends := 0
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		sends++
		if len(message.ID) != 64 || message.Body != "test body" {
			t.Fatal(message)
		}
		return nil
	}
	if err := api.deliverOneMail(ctx, session); err != nil {
		t.Fatal(err)
	}
	if err := api.deliverOneMail(ctx, session); !errors.Is(err, store.ErrMissing) {
		t.Fatal(err)
	}
	if queued, err := api.queueMail(ctx, session, "test", "event", message); err != nil || queued || sends != 1 {
		t.Fatal(queued, err, sends)
	}
}

func TestMailSenderLogoutCancelsInflightAndKeepsOtherSourcesSeparate(t *testing.T) {
	api, session := setupMailQueue(t)
	ctx := context.Background()
	started, stopped := make(chan struct{}), make(chan struct{})
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		close(started)
		<-ctx.Done()
		close(stopped)
		return ctx.Err()
	}
	api.queueMail(ctx, session, "test", "event", notifications.MailMessage{Subject: "test", Body: "test"})
	api.scheduleMail()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("sender did not start")
	}
	api.scheduleMail()
	if err := api.accounts.Stop(ctx, session.Account, false); err != nil {
		t.Fatal(err)
	}
	select {
	case <-stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("logout did not cancel sender")
	}
	if due, err := api.database.DueMailAccounts(ctx, time.Now().Add(time.Hour)); err != nil || len(due) != 0 {
		t.Fatal(due, err)
	}
}

func TestStoppingOneAccountDuringConcurrentMailDelivery(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	api.accounts.mutex.Lock()
	api.accounts.mailTick = nil
	api.accounts.mutex.Unlock()
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(ctx, account)
		cookie := loginCookie(t, handler, account)
		config := `{"smtp_host":"smtp.example.invalid","smtp_port":465,"smtp_security":"ssl","from_email":"sender@example.invalid","to_email":"` + account + `@example.invalid"}`
		if response := apiRequest(handler, "PUT", "/api/system-settings/mail", config, cookie); response.Code != 200 {
			t.Fatal(response)
		}
		generation, err := api.database.Generation(ctx, account)
		if err != nil {
			t.Fatal(err)
		}
		queued, err := api.queueMail(ctx, store.Session{Account: account, Generation: generation}, "test", "shared-event", notifications.MailMessage{Subject: account, Body: "fixture"})
		if err != nil || !queued {
			t.Fatal(account, queued, err)
		}
	}
	started := make(chan string, 2)
	finished := make(chan string, 2)
	release := make(chan struct{})
	defer close(release)
	api.sendMail = func(ctx context.Context, config notifications.MailConfig, message notifications.MailMessage) error {
		if config.To != message.Subject+"@example.invalid" {
			t.Errorf("recipient mismatch for %s", message.Subject)
		}
		started <- message.Subject
		select {
		case <-ctx.Done():
			finished <- "canceled " + message.Subject
			return ctx.Err()
		case <-release:
			finished <- "sent " + message.Subject
			return nil
		}
	}
	next := func(events <-chan string) string {
		t.Helper()
		select {
		case event := <-events:
			return event
		case <-time.After(5 * time.Second):
			t.Fatal("mail scheduler timed out")
			return ""
		}
	}
	api.scheduleMail()
	if next(started) == next(started) {
		t.Fatal("expected both accounts to enter delivery")
	}
	if err := api.accounts.Stop(ctx, "student-a", false); err != nil {
		t.Fatal(err)
	}
	if event := next(finished); event != "canceled student-a" {
		t.Fatal(event)
	}
	select {
	case release <- struct{}{}:
	case <-time.After(5 * time.Second):
		t.Fatal("remaining sender unavailable")
	}
	if event := next(finished); event != "sent student-b" {
		t.Fatal(event)
	}
}
