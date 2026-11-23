package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) readMailConfig(ctx context.Context, session store.Session) (notifications.MailConfig, error) {
	config := notifications.DefaultMailConfig()
	generation, err := api.database.Generation(ctx, session.Account)
	if err != nil {
		return config, err
	}
	if generation != session.Generation {
		return config, store.ErrAccess
	}
	payload, err := api.database.ReadSecret(ctx, session.Account, "mail-config")
	if errors.Is(err, store.ErrMissing) {
		return config, nil
	}
	if err != nil {
		return config, err
	}
	if json.Unmarshal(payload, &config) != nil || !config.Valid(false) {
		return config, store.ErrConflict
	}
	return config, nil
}

func (api *API) mailSettings(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	release, err := api.resources.acquireAccount(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	config, err := api.readMailConfig(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if request.Method == "GET" {
		writeJSON(response, 200, config.Public())
		return
	}
	input := struct {
		notifications.MailConfig
		Clear bool `json:"clear_smtp_password"`
	}{MailConfig: config}
	input.Password = ""
	if !decodeJSON(response, request, &input) {
		return
	}
	input.Host, input.From, input.To = strings.TrimSpace(input.Host), strings.TrimSpace(input.From), strings.TrimSpace(input.To)
	if input.Password == "" && !input.Clear {
		input.Password = config.Password
	}
	if !input.MailConfig.Valid(false) {
		writeJSON(response, 422, map[string]string{"detail": "SMTP服务器、端口、安全方式或邮箱地址无效"})
		return
	}
	payload, _ := json.Marshal(input.MailConfig)
	if err := api.database.SaveSecretForSession(request.Context(), session, "mail-config", payload); err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "config": input.MailConfig.Public()})
}

func (api *API) testMail(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	config, err := api.readMailConfig(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if !config.Valid(true) {
		writeJSON(response, 400, map[string]string{"detail": "请先配置SMTP服务器、发件地址、收件地址及所需密码"})
		return
	}
	ctx, cancel := context.WithCancel(request.Context())
	defer cancel()
	stop := context.AfterFunc(api.accounts.context, cancel)
	defer stop()
	if runtime := api.accounts.Runtime(session); runtime != nil {
		stopAccount := context.AfterFunc(runtime.Context, cancel)
		defer stopAccount()
	}
	message := notifications.MailMessage{Subject: "[NEU 教务工具箱] 系统邮件配置测试", Body: "这是一封系统邮件配置测试邮件。\n\n收到此邮件表示当前SMTP配置能够发送邮件。本测试不触发成绩检查、选课请求或登录恢复。"}
	if err := api.sendMail(ctx, config, message); err != nil {
		writeJSON(response, 502, map[string]string{"detail": "测试邮件发送失败，请检查邮件配置与网络"})
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "message": "测试邮件已发送"})
}
