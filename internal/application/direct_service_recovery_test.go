package application

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type countedServiceClient struct {
	testCampusClient
	requests, recoveries int
	confirmed, ready     bool
}

func TestDirectRecoveryFromHandshakeToRead(t *testing.T) {
	for _, service := range []string{"jwxk", "cxcy"} {
		t.Run(service+"/handshake", func(t *testing.T) { checkDirectRecoveryFromHandshakeToRead(t, service, false) })
		t.Run(service+"/business", func(t *testing.T) { checkDirectRecoveryFromHandshakeToRead(t, service, true) })
	}
}

func checkDirectRecoveryFromHandshakeToRead(t *testing.T, service string, businessExpiry bool) {
	host, callback, home, path, cookie := "jwxk.neu.edu.cn", "/xsxk/auth/cas", "/xsxk/profile/index.html", "/xsxk/web/now", "token=recovered; Path=/xsxk/; Secure"
	if service == "cxcy" {
		host, callback, home, path, cookie = "cxcy.neu.edu.cn", "/ucenter/auth/caslogin?type=student", "/ucenter/home", "/popscience/comp/ucenter/index", "session=recovered; Path=/; Secure"
	}
	callbackPath, _, _ := strings.Cut(callback, "?")
	ctx := context.Background()
	api, _ := testApplication(t, "remote-multi")
	previous := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = previous })
	posts, reads, denied := 0, 0, 0
	http.DefaultTransport = releaseTransport(func(request *http.Request) (*http.Response, error) {
		response := &http.Response{Request: request, StatusCode: 200, Header: make(http.Header)}
		body := "home"
		switch request.URL.Host + request.URL.Path {
		case "pass.neu.edu.cn/tpass/login":
			if request.URL.Query().Get("service") != "https://"+host+callback {
				t.Fatal("wrong CAS target")
			}
			if request.Method == http.MethodGet {
				body = `<input type="hidden" name="lt" value="ticket">`
			} else {
				if request.ParseForm() != nil || request.PostForm.Get("pd") != "test-only" || request.PostForm.Get("un") != "student" || request.PostForm.Get("lt") != "ticket" {
					t.Fatal("incorrect credentials or login ticket")
				}
				posts++
				response.StatusCode = 302
				response.Header.Set("Location", "https://"+host+callback)
			}
		case host + callbackPath:
			if posts == 0 && !businessExpiry {
				denied++
				response.StatusCode = 401
			} else {
				response.StatusCode = 302
				response.Header.Set("Set-Cookie", cookie)
				response.Header.Set("Location", home)
			}
		case host + home:
		case "jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do":
			body = `{"code":0,"datas":{"userId":"student"}}`
		case host + path:
			if businessExpiry && posts == 0 {
				denied++
				response.StatusCode = 401
				break
			}
			if service == "cxcy" {
				if session, err := request.Cookie("session"); err != nil || session.Value != "recovered" || request.Header.Get("Authorization") != "" {
					t.Fatal("festival read missing isolated session")
				}
			} else if request.Header.Get("Authorization") != "recovered" {
				t.Fatal("read missing recovered token")
			}
			reads++
			body = `{"code":200}`
		default:
			t.Fatal("unexpected request", request.URL)
		}
		response.Body = io.NopCloser(strings.NewReader(body))
		return response, nil
	})
	client, err := campus.NewClient("student")
	if err != nil {
		t.Fatal(err)
	}
	if err := client.VerifySaved(ctx); err != nil {
		t.Fatal(err)
	}
	if err := api.database.AddAccount(ctx, "student"); err != nil {
		t.Fatal(err)
	}
	generation, err := api.database.Generation(ctx, "student")
	if err != nil {
		t.Fatal(err)
	}
	if err := api.accounts.installClient(ctx, client, "test-only", true, generation); err != nil {
		t.Fatal(err)
	}
	runtime := api.accounts.Runtime(store.Session{Account: "student", Generation: generation})
	payload, err := runtime.RequestService(ctx, campus.ServiceRequest{Service: service, Mode: "direct", Method: http.MethodGet, Path: path, ReadOnly: true})
	expectedDenied := 1
	if businessExpiry {
		expectedDenied = 2
	}
	if err != nil || string(payload) != `{"code":200}` || posts != 1 || reads != 1 || denied != expectedDenied {
		t.Fatalf("payload=%q err=%v posts=%d reads=%d denied=%d", payload, err, posts, reads, denied)
	}
}

func (client *countedServiceClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	client.requests++
	if client.ready {
		return []byte("records"), nil
	}
	return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED", ConfirmedServiceExpiry: client.confirmed}
}

func (client *countedServiceClient) RecoverDirectService(ctx context.Context, service, password string) error {
	client.recoveries++
	if password != "test-only" || service != "jwxk" && service != "cxcy" {
		return errors.New("unexpected recovery credentials or service")
	}
	client.ready = true
	return nil
}

func TestRuntimeDirectRecoveryBoundaries(t *testing.T) {
	for _, scenario := range []struct {
		name, service                      string
		read, remember, confirmed, recover bool
	}{
		{"selection", "jwxk", true, true, true, true},
		{"festival", "cxcy", true, true, true, true},
		{"write", "jwxk", false, true, true, false},
		{"without remembered credentials", "cxcy", true, false, true, false},
		{"ambiguous expiry", "jwxk", true, true, false, false},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			ctx := context.Background()
			api, _ := testApplication(t, "remote-multi")
			client := &countedServiceClient{confirmed: scenario.confirmed}
			client.account = "student"
			client.active.Store(true)
			if err := api.database.AddAccount(ctx, client.account); err != nil {
				t.Fatal(err)
			}
			generation, err := api.database.Generation(ctx, client.account)
			if err != nil {
				t.Fatal(err)
			}
			if err := api.accounts.installClient(ctx, client, "test-only", scenario.remember, generation); err != nil {
				t.Fatal(err)
			}
			runtime := api.accounts.Runtime(store.Session{Account: client.account, Generation: generation})
			input := campus.ServiceRequest{Service: scenario.service, Mode: "direct", ReadOnly: scenario.read}
			payload, err := runtime.RequestService(ctx, input)
			if !scenario.recover {
				if err == nil || client.requests != 1 || client.recoveries != 0 {
					t.Fatal("unexpected retry", err, client.requests, client.recoveries)
				}
				return
			}
			if err != nil || string(payload) != "records" || client.requests != 2 || client.recoveries != 1 {
				t.Fatal("read did not recover exactly once", err, client.requests, client.recoveries)
			}
			client.ready = false
			if _, err := runtime.RequestService(ctx, input); err == nil || client.requests != 3 || client.recoveries != 1 {
				t.Fatal("recovered again during cooldown", err, client.requests, client.recoveries)
			}
		})
	}
}
