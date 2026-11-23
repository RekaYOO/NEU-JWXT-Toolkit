package notifications

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"fmt"
	"mime"
	"mime/multipart"
	"mime/quotedprintable"
	"net"
	"net/mail"
	"net/smtp"
	"net/textproto"
	"strconv"
	"strings"
	"time"
)

type MailConfig struct {
	Host     string `json:"smtp_host"`
	Port     int    `json:"smtp_port"`
	Security string `json:"smtp_security"`
	Username string `json:"smtp_username"`
	Password string `json:"smtp_password"`
	From     string `json:"from_email"`
	To       string `json:"to_email"`
}

func DefaultMailConfig() MailConfig { return MailConfig{Port: 465, Security: "ssl"} }

func (config MailConfig) Valid(complete bool) bool {
	if config.Port < 1 || config.Port > 65535 || config.Security != "ssl" && config.Security != "starttls" && config.Security != "none" || len(config.Host) > 255 || len(config.Username) > 255 || len(config.Password) > 2000 || strings.ContainsAny(config.Host, "\r\n\t /\\@") || strings.ContainsAny(config.Username, "\r\n\x00") {
		return false
	}
	for _, address := range []string{config.From, config.To} {
		if address == "" && !complete {
			continue
		}
		parsed, err := mail.ParseAddress(address)
		if err != nil || parsed.Address != address || len(address) > 255 || strings.ContainsAny(address, "\r\n") {
			return false
		}
	}
	return !complete || config.Host != "" && (config.Username == "" || config.Password != "")
}

func (config MailConfig) Public() map[string]any {
	return map[string]any{"smtp_host": config.Host, "smtp_port": config.Port, "smtp_security": config.Security, "smtp_username": config.Username, "from_email": config.From, "to_email": config.To, "smtp_password_configured": config.Password != ""}
}

type MailMessage struct {
	ID            string            `json:"message_id,omitempty"`
	Context       map[string]string `json:"context,omitempty"`
	EventScope    string            `json:"event_scope,omitempty"`
	EventRevision int64             `json:"event_revision,omitempty"`
	Subject       string            `json:"subject"`
	Body          string            `json:"body"`
	HTML          string            `json:"html_body"`
}

func (message MailMessage) Valid() bool {
	return message.Subject != "" && len(message.Subject) <= 500 && len(message.Body)+len(message.HTML) <= 1024*1024 && !strings.ContainsAny(message.Subject, "\r\n")
}

type mailAuth struct {
	username, password, host string
	login                    bool
	step                     int
}

func (auth *mailAuth) Start(server *smtp.ServerInfo) (string, []byte, error) {
	if server.Name != auth.host {
		return "", nil, errors.New("SMTP_AUTH_HOST_MISMATCH")
	}
	for _, mechanism := range server.Auth {
		if mechanism == "PLAIN" {
			return "PLAIN", []byte("\x00" + auth.username + "\x00" + auth.password), nil
		}
	}
	for _, mechanism := range server.Auth {
		if mechanism == "LOGIN" {
			auth.login = true
			return "LOGIN", nil, nil
		}
	}
	return "", nil, errors.New("SMTP_AUTH_UNSUPPORTED")
}

func (auth *mailAuth) Next(challenge []byte, more bool) ([]byte, error) {
	if !more {
		return nil, nil
	}
	if !auth.login || auth.step >= 2 {
		return nil, errors.New("SMTP_AUTH_CHALLENGE_INVALID")
	}
	auth.step++
	if auth.step == 1 {
		return []byte(auth.username), nil
	}
	return []byte(auth.password), nil
}

func encodeMail(config MailConfig, message MailMessage) ([]byte, error) {
	if !config.Valid(true) || !message.Valid() {
		return nil, errors.New("invalid mail message")
	}
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return nil, err
	}
	messageID := hex.EncodeToString(identifier)
	if message.ID != "" {
		if decoded, err := hex.DecodeString(message.ID); err != nil || len(decoded) != 32 {
			return nil, errors.New("invalid message identifier")
		}
		messageID = message.ID
	}
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	for _, part := range []struct{ kind, content string }{{"text/plain", message.Body}, {"text/html", message.HTML}} {
		if part.kind == "text/html" && part.content == "" {
			continue
		}
		header := textproto.MIMEHeader{"Content-Type": {part.kind + "; charset=utf-8"}, "Content-Transfer-Encoding": {"quoted-printable"}}
		output, err := writer.CreatePart(header)
		if err != nil {
			return nil, err
		}
		encoder := quotedprintable.NewWriter(output)
		if _, err := encoder.Write([]byte(part.content)); err != nil {
			return nil, err
		}
		if err := encoder.Close(); err != nil {
			return nil, err
		}
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}
	header := fmt.Sprintf("From: %s\r\nTo: %s\r\nSubject: %s\r\nDate: %s\r\nMessage-ID: <%s@neu-toolbox.local>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=%q\r\n\r\n", config.From, config.To, mime.QEncoding.Encode("utf-8", message.Subject), time.Now().Format(time.RFC1123Z), messageID, writer.Boundary())
	return append([]byte(header), body.Bytes()...), nil
}

func SendMail(ctx context.Context, config MailConfig, message MailMessage) error {
	payload, err := encodeMail(config, message)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	dialer := net.Dialer{}
	connection, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(config.Host, strconv.Itoa(config.Port)))
	if err != nil {
		return errors.New("SMTP_CONNECTION_FAILED")
	}
	defer connection.Close()
	rawConnection := connection
	stop := context.AfterFunc(ctx, func() { rawConnection.Close() })
	defer stop()
	deadline, _ := ctx.Deadline()
	connection.SetDeadline(deadline)
	security := &tls.Config{ServerName: config.Host, MinVersion: tls.VersionTLS12}
	if config.Security == "ssl" {
		secured := tls.Client(connection, security)
		if err := secured.HandshakeContext(ctx); err != nil {
			return errors.New("SMTP_TLS_FAILED")
		}
		connection = secured
	}
	client, err := smtp.NewClient(connection, config.Host)
	if err != nil {
		return errors.New("SMTP_GREETING_FAILED")
	}
	defer client.Close()
	if config.Security == "starttls" {
		if err := client.StartTLS(security); err != nil {
			return errors.New("SMTP_TLS_FAILED")
		}
	}
	if config.Username != "" {
		if err := client.Auth(&mailAuth{username: config.Username, password: config.Password, host: config.Host}); err != nil {
			return errors.New("SMTP_AUTH_FAILED")
		}
	}
	if err := client.Mail(config.From); err != nil {
		return errors.New("SMTP_SENDER_REJECTED")
	}
	if err := client.Rcpt(config.To); err != nil {
		return errors.New("SMTP_RECIPIENT_REJECTED")
	}
	output, err := client.Data()
	if err != nil {
		return errors.New("SMTP_DATA_REJECTED")
	}
	if _, err := output.Write(payload); err != nil {
		return errors.New("SMTP_DELIVERY_UNCERTAIN")
	}
	if err := output.Close(); err != nil {
		return errors.New("SMTP_DELIVERY_UNCERTAIN")
	}
	client.Quit()
	return nil
}
