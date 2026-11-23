package application

import (
	"context"
	"html"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) attachRecoveryContext(ctx context.Context, session store.Session, target, source, scope string, message *notifications.MailMessage) (string, error) {
	settings, _, err := api.readRecoverySettings(ctx, session)
	if err != nil || settings.BaseURL == "" {
		return "", err
	}
	recovery, err := api.database.CreateRecoveryContext(ctx, session, target, source, scope, settings.Epoch, time.Duration(settings.Hours)*time.Hour)
	if err != nil {
		return "", err
	}
	if message.Context == nil {
		message.Context = map[string]string{}
	}
	message.Context["recovery_id"] = recovery.ID
	return recovery.ID, nil
}

func (api *API) renderRecoveryMail(ctx context.Context, entry store.MailEntry, message *notifications.MailMessage) error {
	identifier := message.Context["recovery_id"]
	if identifier == "" {
		return nil
	}
	token, err := api.database.RecoveryToken(ctx, identifier)
	if err != nil {
		return err
	}
	recovery, err := api.database.RecoveryContext(ctx, token)
	if err != nil {
		return err
	}
	if recovery.Account != entry.Session.Account || recovery.Generation != entry.Session.Generation || recovery.Source != entry.Source {
		return store.ErrMissing
	}
	settings, _, err := api.readRecoverySettings(ctx, entry.Session)
	if err != nil {
		return err
	}
	if settings.Epoch != recovery.Epoch || settings.BaseURL == "" {
		return store.ErrMissing
	}
	link := settings.BaseURL + "/auth-recovery/" + token
	expiry := time.Unix(recovery.Expires, 0).In(time.FixedZone("Asia/Shanghai", 8*60*60)).Format("2006-01-02 15:04:05")
	message.Body += "\n\n一次性登录恢复：" + link + "\n有效期至北京时间 " + expiry + "。此链接仅用于恢复登录，不提供个人资料访问权限。"
	message.HTML += "<p><a href=\"" + html.EscapeString(link) + "\">恢复学校登录</a></p><p>有效期至北京时间 " + expiry + "。此链接仅用于恢复登录，不提供个人资料访问权限。</p>"
	return nil
}
