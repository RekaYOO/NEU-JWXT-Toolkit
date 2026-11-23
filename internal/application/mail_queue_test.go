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
