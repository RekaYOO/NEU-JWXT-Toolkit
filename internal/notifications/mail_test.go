package notifications

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net"
	"net/mail"
	"net/smtp"
	"strings"
	"testing"
	"time"
)

func TestMailSMTPDeliveryAndRequiredTLS(t *testing.T) {
	for _, security := range []string{"none", "starttls"} {
		t.Run(security, func(t *testing.T) {
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			received := make(chan string, 1)
			go func() {
				connection, err := listener.Accept()
				if err != nil {
					received <- ""
					return
				}
				defer connection.Close()
				connection.SetDeadline(time.Now().Add(5 * time.Second))
				fmt.Fprint(connection, "220 local-test ESMTP\r\n")
				reader := bufio.NewReader(connection)
				var data strings.Builder
				for {
					line, err := reader.ReadString('\n')
					if err != nil {
						received <- data.String()
						return
					}
					switch {
					case strings.HasPrefix(line, "EHLO"), strings.HasPrefix(line, "HELO"):
						fmt.Fprint(connection, "250 local-test\r\n")
					case strings.HasPrefix(line, "MAIL"), strings.HasPrefix(line, "RCPT"):
						fmt.Fprint(connection, "250 accepted\r\n")
					case strings.HasPrefix(line, "DATA"):
						fmt.Fprint(connection, "354 continue\r\n")
						for {
							line, err := reader.ReadString('\n')
							if err != nil {
								received <- ""
								return
							}
							if line == ".\r\n" {
								break
							}
							data.WriteString(line)
						}
						fmt.Fprint(connection, "250 queued\r\n")
					case strings.HasPrefix(line, "QUIT"):
						fmt.Fprint(connection, "221 bye\r\n")
						received <- data.String()
						return
					default:
						fmt.Fprint(connection, "502 unsupported\r\n")
					}
				}
			}()
			config := MailConfig{Host: "127.0.0.1", Port: listener.Addr().(*net.TCPAddr).Port, Security: security, From: "sender@example.invalid", To: "recipient@example.invalid"}
			err = SendMail(context.Background(), config, MailMessage{Subject: "中文测试", Body: "纯文本内容", HTML: "<p>同一内容</p>"})
			payload := <-received
			if security == "starttls" {
				if err == nil || payload != "" {
					t.Fatal("required TLS downgraded", err, payload)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			message, err := mail.ReadMessage(strings.NewReader(payload))
			if err != nil {
				t.Fatal(err)
			}
			subject, err := new(mime.WordDecoder).DecodeHeader(message.Header.Get("Subject"))
			if err != nil || subject != "中文测试" || message.Header.Get("To") != config.To {
				t.Fatal("invalid message headers", subject, message.Header, err)
			}
			kind, params, err := mime.ParseMediaType(message.Header.Get("Content-Type"))
			if err != nil || kind != "multipart/alternative" {
				t.Fatal("invalid MIME", kind, err)
			}
			parts := multipart.NewReader(message.Body, params["boundary"])
			for _, expected := range []struct{ kind, body string }{{"text/plain", "纯文本内容"}, {"text/html", "<p>同一内容</p>"}} {
				part, err := parts.NextPart()
				if err != nil {
					t.Fatal(err)
				}
				kind, params, err := mime.ParseMediaType(part.Header.Get("Content-Type"))
				if err != nil || kind != expected.kind || params["charset"] != "utf-8" {
					t.Fatal("invalid part", kind, params, err)
				}
				body, err := io.ReadAll(part)
				if err != nil || string(body) != expected.body {
					t.Fatal("invalid decoded body", string(body), err)
				}
			}
			if _, err := parts.NextPart(); err != io.EOF {
				t.Fatal("unexpected trailing part", err)
			}
		})
	}
}

func TestMailAuthMechanismsAndHeaderInjection(t *testing.T) {
	for _, mechanism := range []string{"PLAIN", "LOGIN"} {
		auth := &mailAuth{username: "user", password: "secret", host: "smtp.example.invalid"}
		name, initial, err := auth.Start(&smtp.ServerInfo{Name: auth.host, Auth: []string{mechanism}})
		if err != nil || name != mechanism {
			t.Fatal(name, err)
		}
		if mechanism == "PLAIN" && string(initial) != "\x00user\x00secret" {
			t.Fatal("bad PLAIN payload")
		}
		if mechanism == "LOGIN" {
			username, _ := auth.Next(nil, true)
			password, _ := auth.Next(nil, true)
			if string(username) != "user" || string(password) != "secret" {
				t.Fatal("bad LOGIN exchange")
			}
			if _, err := auth.Next(nil, true); err == nil {
				t.Fatal("extra challenge accepted")
			}
		}
	}
	config := MailConfig{Host: "smtp.example.invalid", Port: 465, Security: "ssl", From: "from@example.invalid", To: "to@example.invalid"}
	stableID := strings.Repeat("ab", 32)
	for range 2 {
		payload, err := encodeMail(config, MailMessage{ID: stableID, Subject: "retry", Body: "body"})
		if err != nil || !strings.Contains(string(payload), "Message-ID: <"+stableID+"@neu-toolbox.local>") {
			t.Fatal("unstable retry identifier", err)
		}
	}
	if _, err := encodeMail(config, MailMessage{Subject: "title\r\nBcc: bad@example.invalid"}); err == nil {
		t.Fatal("header injection accepted")
	}
	config.To += "\r\nBcc: bad@example.invalid"
	if config.Valid(true) {
		t.Fatal("address injection accepted")
	}
}

func TestMailCancellationClosesBlockedConnection(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	connected := make(chan struct{})
	closed := make(chan struct{})
	go func() {
		connection, err := listener.Accept()
		if err != nil {
			close(connected)
			close(closed)
			return
		}
		defer connection.Close()
		connection.SetDeadline(time.Now().Add(5 * time.Second))
		close(connected)
		buffer := make([]byte, 1)
		connection.Read(buffer)
		close(closed)
	}()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan error, 1)
	go func() {
		finished <- SendMail(ctx, MailConfig{Host: "127.0.0.1", Port: listener.Addr().(*net.TCPAddr).Port, Security: "none", From: "from@example.invalid", To: "to@example.invalid"}, MailMessage{Subject: "test", Body: "test"})
	}()
	<-connected
	cancel()
	select {
	case err := <-finished:
		if err == nil {
			t.Fatal("cancelled send reported success")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancelled mail send remained blocked")
	}
	<-closed
}
