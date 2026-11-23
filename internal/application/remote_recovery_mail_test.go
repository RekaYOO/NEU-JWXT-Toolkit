package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestRecoveryMailStoresOnlyContextAndRendersAtDelivery(t *testing.T) {
	api, session := setupMailQueue(t)
	ctx := context.Background()
	if _, err := api.database.SaveForSession(ctx, session, "auth-recovery", "config", []byte(`{"public_base_url":"https://toolbox.example.invalid","link_ttl_hours":3,"epoch":"first"}`), 0, false); err != nil {
		t.Fatal(err)
	}
	message := notifications.MailMessage{Subject: "test recovery", Body: "test body", HTML: "<p>test body</p>"}
	identifier, err := api.attachRecoveryContext(ctx, session, "primary", "test", "lost", &message)
	if err != nil || identifier == "" {
		t.Fatal("context not created", err)
	}
	if queued, err := api.queueMail(ctx, session, "test", "lost", message); err != nil || !queued {
		t.Fatal(queued, err)
	}
	entry, err := api.database.ClaimMail(ctx, session, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(entry.Payload), "/auth-recovery/") || strings.Contains(string(entry.Payload), "https://") {
		t.Fatal("signed URL persisted in queue")
	}
	var queued notifications.MailMessage
	if err := json.Unmarshal(entry.Payload, &queued); err != nil {
		t.Fatal(err)
	}
	if err := api.renderRecoveryMail(ctx, entry, &queued); err != nil {
		t.Fatal(err)
	}
	token, err := api.database.RecoveryToken(ctx, identifier)
	if err != nil || !strings.Contains(queued.Body, "/auth-recovery/"+token) || !strings.Contains(queued.HTML, "/auth-recovery/"+token) || !strings.Contains(queued.Body, "北京时间") {
		t.Fatal("rendered recovery information missing", err)
	}
	foreign := entry
	foreign.Session.Account = "other"
	if err := api.renderRecoveryMail(ctx, foreign, &message); !errors.Is(err, store.ErrMissing) {
		t.Fatal("context exposed through another account's mail", err)
	}
	if err := api.database.FinishRecoveryContext(ctx, token, "cancelled"); err != nil {
		t.Fatal(err)
	}
	if err := api.renderRecoveryMail(ctx, entry, &message); !errors.Is(err, store.ErrMissing) {
		t.Fatal("cancelled link rendered", err)
	}
}

func TestDeliveryDiscardsInvalidRecoveryLinkWithoutSending(t *testing.T) {
	api, session := setupMailQueue(t)
	ctx := context.Background()
	if _, err := api.database.SaveForSession(ctx, session, "auth-recovery", "config", []byte(`{"public_base_url":"https://toolbox.example.invalid","link_ttl_hours":3,"epoch":"first"}`), 0, false); err != nil {
		t.Fatal(err)
	}
	message := notifications.MailMessage{Subject: "test recovery", Body: "test body"}
	identifier, err := api.attachRecoveryContext(ctx, session, "primary", "test", "lost", &message)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := api.queueMail(ctx, session, "test", "lost", message); err != nil {
		t.Fatal(err)
	}
	token, err := api.database.RecoveryToken(ctx, identifier)
	if err != nil {
		t.Fatal(err)
	}
	if err := api.database.FinishRecoveryContext(ctx, token, "cancelled"); err != nil {
		t.Fatal(err)
	}
	sends := 0
	api.sendMail = func(context.Context, notifications.MailConfig, notifications.MailMessage) error { sends++; return nil }
	if err := api.deliverOneMail(ctx, session); err != nil || sends != 0 {
		t.Fatal("invalid recovery mail reached sender", err)
	}
}
