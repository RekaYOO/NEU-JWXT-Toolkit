package campus

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
)

func TestDirectRecoveryDistinguishesConnectionFromUncertainCredentialSubmission(t *testing.T) {
	for _, phase := range []string{"before", "after"} {
		t.Run(phase, func(t *testing.T) {
			client, _ := NewClient("student")
			posts := 0
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				if request.Method == "POST" {
					posts++
				}
				if phase == "before" || request.Method == "POST" {
					return nil, errors.New("network unavailable")
				}
				return mockResponse(request, 200, `<input type="hidden" name="lt" value="test">`), nil
			})
			err := client.LoginDirect(context.Background(), "test-password")
			var failure *Error
			if !errors.As(err, &failure) {
				t.Fatal(err)
			}
			if phase == "before" && (failure.Code != "DIRECT_ACCESS_FAILED" || posts != 0) {
				t.Fatal(failure, posts)
			}
			if phase == "after" && (failure.Code != "UPSTREAM_UNAVAILABLE" || posts != 1) {
				t.Fatal(failure, posts)
			}
		})
	}
}

func TestSessionProbePreservesLoginOnTransientFailureAndExpiresOnlyOnIdentityEvidence(t *testing.T) {
	for _, mode := range []string{"direct", "webvpn"} {
		for _, scenario := range []struct {
			name, body string
			status     int
			active     bool
		}{{"healthy", `{"code":0,"datas":{"userId":"student"}}`, 200, true}, {"outage", "unavailable", 503, true}, {"malformed", "invalid response", 200, true}, {"expired", `{"code":401,"message":"未登录"}`, 200, false}, {"foreign", `{"code":0,"datas":{"userId":"other"}}`, 200, false}} {
			t.Run(mode+"-"+scenario.name, func(t *testing.T) {
				client, _ := NewClient("student")
				client.setStatus(true, mode)
				calls := 0
				client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
					calls++
					if !strings.HasSuffix(request.URL.Path, "currentUser.do") {
						t.Fatal("probe attempted authentication")
					}
					if active, _ := client.Status(); !active {
						t.Fatal("probe temporarily published logged out state")
					}
					return mockResponse(request, scenario.status, scenario.body), nil
				})
				err := client.ProbeSession(context.Background())
				if scenario.name == "healthy" && err != nil {
					t.Fatal(err)
				}
				if scenario.name != "healthy" && err == nil {
					t.Fatal("invalid response accepted")
				}
				if active, actualMode := client.Status(); active != scenario.active || actualMode != mode || calls != 1 {
					t.Fatal(active, actualMode, calls)
				}
			})
		}
	}
}

func TestSavedDirectSessionNeedsMatchingOfficialIdentity(t *testing.T) {
	for body, expected := range map[string]string{
		"<html>maintenance</html>": "UPSTREAM_RESPONSE_INVALID",
		`{}`: "UPSTREAM_RESPONSE_INVALID",
		`{"code":503,"message":"登录服务维护中"}`: "UPSTREAM_RESPONSE_INVALID",
		`{"code":0,"datas":{}}`: "UPSTREAM_RESPONSE_INVALID",
		`{"code":401,"message":"未登录"}`: "AUTH_REQUIRED",
		`{"code":1,"message":"会话已过期，请重新登录"}`: "AUTH_REQUIRED",
	} {
		client, _ := NewClient("student")
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			return mockResponse(request, 200, body), nil
		})
		failure, ok := client.VerifySaved(context.Background()).(*Error)
		if !ok || failure.Code != expected {
			t.Fatal(body, expected, failure)
		}
	}
	for _, identity := range []string{"student", "other"} {
		client, _ := NewClient("student")
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			return mockResponse(request, 200, `{"code":0,"datas":{"userId":"`+identity+`"}}`), nil
		})
		err := client.VerifySaved(context.Background())
		active, _ := client.Status()
		if identity == "student" && (err != nil || !active) {
			t.Fatal(err, active)
		}
		if identity == "other" && (err == nil || active) {
			t.Fatal("foreign saved session accepted")
		}
	}
}
