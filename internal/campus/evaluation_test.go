package campus

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestEvaluationTicketExchangeAndTokenReuse(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	claims := base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprintf(`{"exp":%d}`, time.Now().Add(time.Hour).Unix())))
	token := "header." + claims + ".signature"
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		switch calls {
		case 1:
			if request.URL.Host != "pass.neu.edu.cn" || request.URL.Query().Get("service") != evaluationOrigin+"/caslogin" || request.Header.Get("Authorization") != "" {
				t.Fatal("incorrect CAS request")
			}
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", evaluationOrigin+"/caslogin?ticket=ST-test")
			return response, nil
		case 2:
			if request.URL.Path != "/api/cas_ticket_verify" || request.URL.Query().Get("ticket") != "ST-test" {
				t.Fatal("incorrect ticket exchange")
			}
			return mockResponse(request, 200, `{"accessToken":"`+token+`"}`), nil
		default:
			if request.Method != "POST" || request.Header.Get("Authorization") != "Bearer "+token || request.Header.Get("X-Tenant-ID") != "default" {
				t.Fatal("missing evaluation credentials")
			}
			payload, _ := io.ReadAll(request.Body)
			if string(payload) != `{"taskid":"task"}` {
				t.Fatal(string(payload))
			}
			return mockResponse(request, 200, `{"items":[],"rowCount":0}`), nil
		}
	})
	for attempt := 0; attempt < 2; attempt++ {
		if _, err := client.EvaluationRequest(context.Background(), "/api/tasks", json.RawMessage(`{"taskid":"task"}`)); err != nil {
			t.Fatal(err)
		}
	}
	if calls != 4 {
		t.Fatal("token was not reused")
	}
	client.Close()
	if client.evaluationToken != "" {
		t.Fatal("token survived logout")
	}
}

func TestEvaluationRejectsRedirectAndDoesNotReplay(t *testing.T) {
	for _, status := range []int{301, 302, 303, 307, 308, 401, 403} {
		client, _ := NewClient("student-a")
		client.setStatus(true, "direct")
		client.evaluationToken = "token"
		client.evaluationExpiry = time.Now().Add(time.Hour)
		calls := 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			calls++
			response := mockResponse(request, status, "")
			response.Header.Set("Location", "https://outside.example/steal")
			return response, nil
		})
		if _, err := client.EvaluationRequest(context.Background(), "/api/save", json.RawMessage(`{}`)); err == nil || calls != 1 {
			t.Fatalf("status=%d calls=%d err=%v", status, calls, err)
		}
		if client.evaluationToken != "" {
			t.Fatal("stale token retained")
		}
		if active, _ := client.Status(); !active {
			t.Fatal("evaluation failure logged out primary service")
		}
	}
}

func TestEvaluationUntrustedTicketAndPathNeverDispatch(t *testing.T) {
	for _, location := range []string{"https://outside.example/caslogin?ticket=secret", evaluationOrigin + "/wrong?ticket=secret", evaluationOrigin + "/caslogin", evaluationOrigin + "/caslogin?ticket=secret#fragment"} {
		client, _ := NewClient("student-a")
		client.setStatus(true, "direct")
		calls := 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			calls++
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", location)
			return response, nil
		})
		if _, err := client.EvaluationRequest(context.Background(), "/api/tasks", nil); err == nil || calls != 1 {
			t.Fatal("untrusted ticket destination accepted")
		}
	}
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	client.http.Transport = transportFunc(func(*http.Request) (*http.Response, error) { t.Fatal("invalid path dispatched"); return nil, nil })
	for _, path := range []string{"https://outside.example/api", "//outside.example/api", "/api/../login", "/api/%2e%2e/login", "/api/test#fragment"} {
		if _, err := client.EvaluationRequest(context.Background(), path, nil); err == nil {
			t.Fatal(path)
		}
	}
}

func TestEvaluationWebVPNRouting(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "webvpn")
	client.evaluationToken = "token"
	client.evaluationExpiry = time.Now().Add(time.Hour)
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		expected, _ := WebVPNURL(evaluationOrigin + "/api/tasks")
		if request.URL.String() != expected || !strings.HasPrefix(request.URL.Path, "/http/") {
			t.Fatal(request.URL)
		}
		return mockResponse(request, 200, `[]`), nil
	})
	if _, err := client.EvaluationRequest(context.Background(), "/api/tasks", nil); err != nil {
		t.Fatal(err)
	}
}
