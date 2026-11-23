package campus

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestServiceOutageBodyCannotTriggerAuthentication(t *testing.T) {
	for _, service := range []string{"jwxk", "cxcy"} {
		for _, status := range []int{429, 500, 502, 503} {
			for _, body := range []string{`<html>统一身份认证 password</html>`, `{"code":401,"msg":"登录失效"}`} {
				if serviceNeedsAuth(service, "/xsxk/web/now", status, []byte(body)) {
					t.Fatalf("%s classified HTTP %d as expired authentication", service, status)
				}
			}
		}
	}
}

func TestDirectServiceCandidateIsolatedFromPrimary(t *testing.T) {
	for service, config := range campusServices {
		t.Run(service, func(t *testing.T) {
			primary, _ := NewClient("student-a")
			primary.setStatus(true, "webvpn")
			origin, _ := url.Parse(casLogin)
			primary.jar.SetCookies(origin, []*http.Cookie{{Name: "original", Value: "primary", Path: "/"}})
			identity := "student-a"
			posts := 0
			primary.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				if _, err := request.Cookie("original"); err == nil {
					t.Fatal("primary cookie sent during independent recovery")
				}
				if request.URL.Host == origin.Host {
					target := "https://" + config.host + config.callback
					if request.URL.Query().Get("service") != target {
						t.Fatal("unexpected CAS target", request.URL)
					}
					if request.Method == http.MethodGet {
						return mockResponse(request, 200, `<input type="hidden" name="lt" value="test-ticket">`), nil
					}
					if request.Method != http.MethodPost || request.ParseForm() != nil || request.PostForm.Get("un") != "student-a" || request.PostForm.Get("pd") != "test-only" || request.PostForm.Get("lt") != "test-ticket" {
						t.Fatal("unexpected service login form")
					}
					posts++
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", target)
					return response, nil
				}
				if request.URL.Host != config.host {
					if request.URL.Host == "jwxt.neu.edu.cn" && request.URL.Path == "/jwapp/sys/homeapp/api/home/currentUser.do" {
						return mockResponse(request, 200, fmt.Sprintf(`{"code":"0","datas":{"userId":%q}}`, identity)), nil
					}
					t.Fatal("unexpected service host", request.URL.Host)
				}
				if request.URL.Path != "/home" {
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", "/home")
					response.Header.Set("Set-Cookie", "token=service; Path=/; Secure")
					return response, nil
				}
				return mockResponse(request, 200, "home"), nil
			})
			candidate, err := primary.authenticateDirectService(context.Background(), service, "test-only")
			if err != nil {
				t.Fatal(err)
			}
			defer candidate.Close()
			if posts != 1 {
				t.Fatal("initial login form count", posts)
			}
			identity = "another-student"
			if rejected, err := primary.authenticateDirectService(context.Background(), service, "test-only"); rejected != nil || err == nil {
				t.Fatal("accepted a candidate for another identity")
			} else if failure, ok := err.(*Error); !ok || failure.Code != "AUTH_ACCOUNT_MISMATCH" {
				t.Fatal("identity mismatch misclassified", err)
			}
			if cookies, mode := primary.Snapshot(); mode != "webvpn" || len(cookies) != 1 || cookies[0].Cookie.Name != "original" {
				t.Fatal("primary changed during independent recovery")
			}
			if posts != 2 {
				t.Fatal("identity mismatch retried password", posts)
			}
			identity = "student-a"
			for _, registered := range campusServices {
				target, _ := url.Parse("https://" + registered.host)
				primary.jar.SetCookies(target, []*http.Cookie{{Name: "old", Value: registered.host, Path: "/"}})
			}
			if err := primary.RecoverDirectService(context.Background(), service, "test-only"); err != nil {
				t.Fatal(err)
			}
			if posts != 3 {
				t.Fatal("unexpected recovery form count", posts)
			}
			for _, registered := range campusServices {
				target, _ := url.Parse("https://" + registered.host + "/home")
				cookies := primary.jar.Cookies(target)
				if len(cookies) != 1 || registered.host == config.host && cookies[0].Name != "token" || registered.host != config.host && cookies[0].Value != registered.host {
					t.Fatal("incorrect cookie replacement", registered.host)
				}
			}
			if active, mode := primary.Status(); !active || mode != "webvpn" || len(primary.jar.Cookies(origin)) != 1 {
				t.Fatal("merge changed primary session")
			}
		})
	}
}

func TestServiceHandshakeOutageIsNotExpiredLogin(t *testing.T) {
	for service, path := range map[string]string{"jwxk": "/xsxk/web/now", "cxcy": "/popscience/comp/ucenter/main/index"} {
		t.Run(service+"/limited401", func(t *testing.T) {
			client, _ := NewClient("student-a")
			client.setStatus(true, "direct")
			calls := 0
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				calls++
				return mockResponse(request, 401, `{"code":401,"msg":"请求频繁，请登录后重试"}`), nil
			})
			for attempt := 0; attempt < 2; attempt++ {
				_, err := client.RequestService(context.Background(), ServiceRequest{Service: service, Method: "GET", Path: path, ReadOnly: true})
				if failure, ok := err.(*Error); !ok || failure.Code != "SERVICE_RATE_LIMIT" || failure.ConfirmedServiceExpiry || calls != 1 {
					t.Fatal("rate limit triggered authentication or retry", err, calls)
				}
			}
		})
	}
	for _, status := range []int{302, 403, 404, 429, 503} {
		client, _ := NewClient("student-a")
		client.setStatus(true, "direct")
		calls := 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			calls++
			response := mockResponse(request, status, "")
			response.Header.Set("Retry-After", "90")
			return response, nil
		})
		input := ServiceRequest{Service: "jwxk", Mode: "direct", Method: http.MethodPost, Path: "/xsxk/web/studentInfo", ReadOnly: true}
		_, err := client.RequestService(context.Background(), input)
		failure, ok := err.(*Error)
		expected := "SERVICE_TEMPORARILY_UNAVAILABLE"
		if status == 302 {
			expected = "UPSTREAM_REDIRECT"
		} else if status == 403 || status == 404 {
			expected = "UPSTREAM_HTTP_ERROR"
		}
		if status == 429 {
			expected = "SERVICE_RATE_LIMIT"
			if !ok || !strings.Contains(failure.Message, "90秒") {
				t.Fatal("missing retry interval", err)
			}
			if _, retryErr := client.RequestService(context.Background(), input); retryErr == nil || calls != 1 {
				t.Fatal("request sent during cooldown", retryErr, calls)
			}
			client.serviceCooldown[input.Service] = time.Now().Add(-time.Second)
		}
		if !ok || failure.Code != expected || calls != 1 {
			t.Fatalf("status=%d calls=%d error=%v", status, calls, err)
		}
		if active, mode := client.Status(); !active || mode != "direct" {
			t.Fatal("primary session changed")
		}
		reads := 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			switch request.URL.Path {
			case "/xsxk/auth/cas":
				response := mockResponse(request, 302, "")
				response.Header.Set("Set-Cookie", "token=recovered; Path=/xsxk/; Secure")
				response.Header.Set("Location", "/xsxk/profile/index.html")
				return response, nil
			case "/xsxk/profile/index.html":
				return mockResponse(request, 200, "home"), nil
			case input.Path:
				reads++
				if request.Header.Get("Authorization") != "recovered" {
					t.Fatal("missing recovered token")
				}
				return mockResponse(request, 200, `{"code":200}`), nil
			}
			t.Fatal("unexpected recovery request", request.URL.Path)
			return nil, nil
		})
		payload, err := client.RequestService(context.Background(), input)
		if err != nil || string(payload) != `{"code":200}` || reads != 1 {
			t.Fatalf("manual retry: reads=%d payload=%q error=%v", reads, payload, err)
		}
	}
}

func TestServiceCASAndOneReadRecovery(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	handshakes, reads := 0, 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Scheme != "https" {
			t.Fatal("cleartext authentication hop")
		}
		switch request.URL.Path {
		case "/xsxk/auth/cas":
			response := mockResponse(request, 302, "")
			if request.URL.Query().Get("ticket") == "" {
				response.Header.Set("Location", "http://pass.neu.edu.cn/tpass/login?service=https%3A%2F%2Fjwxk.neu.edu.cn%2Fxsxk%2Fauth%2Fcas")
			} else {
				handshakes++
				response.Header.Set("Set-Cookie", fmt.Sprintf("token=session-%d; Path=/xsxk/; Secure; HttpOnly", handshakes))
				response.Header.Set("Location", "/xsxk/profile/index.html")
			}
			return response, nil
		case "/tpass/login":
			if request.Header.Get("Authorization") != "" {
				t.Fatal("token sent to CAS")
			}
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", "https://jwxk.neu.edu.cn/xsxk/auth/cas?ticket=test")
			return response, nil
		case "/xsxk/profile/index.html":
			return mockResponse(request, 200, "home"), nil
		case "/xsxk/web/studentInfo":
			reads++
			body, _ := io.ReadAll(request.Body)
			form, _ := url.ParseQuery(string(body))
			token := fmt.Sprintf("session-%d", reads)
			if request.Header.Get("Authorization") != token || form.Get("token") != token || form.Get("extra") != "retained" {
				t.Fatal("token header/form not rebuilt")
			}
			if reads == 1 {
				return mockResponse(request, 200, `{"code":401,"msg":"登录失效"}`), nil
			}
			return mockResponse(request, 200, `{"code":200,"data":{"student":{}}}`), nil
		}
		t.Fatal(request.URL)
		return nil, nil
	})
	form := url.Values{"extra": {"retained"}, "token": {"caller-value"}}
	if _, err := client.RequestService(context.Background(), ServiceRequest{Service: "jwxk", Mode: "direct", Method: "POST", Path: "/xsxk/web/studentInfo", Form: form, ReadOnly: true}); err != nil {
		t.Fatal(err)
	}
	if reads != 2 || handshakes != 2 || form.Get("token") != "caller-value" {
		t.Fatal("unbounded retry or mutated caller input")
	}
	if active, mode := client.Status(); !active || mode != "direct" {
		t.Fatal("primary login changed")
	}
}

func TestOptionalResultAuthFailurePreservesActiveBatch(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	client.serviceReady = map[string]bool{"jwxk:direct": true}
	target, _ := url.Parse("https://jwxk.neu.edu.cn/xsxk/")
	client.jar.SetCookies(target, []*http.Cookie{{Name: "token", Value: "session", Path: "/xsxk/"}})
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		if request.URL.Path == "/xsxk/elective/select" {
			return mockResponse(request, 401, `{"code":401,"msg":"登录失效"}`), nil
		}
		if request.URL.Path != "/xsxk/volunteer/select" {
			t.Fatal("optional feed triggered session rebuild", request.URL)
		}
		return mockResponse(request, 200, `{"code":200,"data":[]}`), nil
	})
	input := ServiceRequest{Service: "jwxk", Mode: "direct", Method: "POST", Path: "/xsxk/elective/select", PreserveSessionOnAuth: true}
	if _, err := client.RequestService(context.Background(), input); err == nil {
		t.Fatal("missing feed failure")
	}
	input.Path = "/xsxk/volunteer/select"
	if _, err := client.RequestService(context.Background(), input); err != nil || calls != 2 {
		t.Fatal(err, calls)
	}
	input.Path = "/xsxk/elective/clazz/list"
	if _, err := client.RequestService(context.Background(), input); err == nil || calls != 2 {
		t.Fatal("preservation allowed outside result feeds")
	}
}

func TestServiceWritesNeverReplayAndBusinessErrorsDoNotRecover(t *testing.T) {
	for _, test := range []struct {
		status    int
		body      string
		read      bool
		wantError bool
	}{
		{307, "", false, true}, {302, "", false, true}, {200, `{"code":401,"msg":"token失效"}`, false, true},
		{200, `{"code":403,"msg":"请求过快，请登录后再试"}`, true, true}, {200, `{"code":401,"msg":"该轮次不支持此类型"}`, true, false},
	} {
		client, _ := NewClient("student-a")
		client.setStatus(true, "direct")
		client.serviceReady = map[string]bool{"jwxk:direct": true}
		target, _ := url.Parse("https://jwxk.neu.edu.cn/xsxk/")
		client.jar.SetCookies(target, []*http.Cookie{{Name: "token", Value: "session", Path: "/xsxk/"}})
		calls := 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			calls++
			response := mockResponse(request, test.status, test.body)
			response.Header.Set("Location", "https://outside.example/steal")
			return response, nil
		})
		_, err := client.RequestService(context.Background(), ServiceRequest{Service: "jwxk", Method: "POST", Path: "/xsxk/elective/volunteer", ReadOnly: test.read})
		if (err != nil) != test.wantError || calls != 1 {
			t.Fatalf("calls=%d err=%v", calls, err)
		}
		if active, _ := client.Status(); !active {
			t.Fatal("child failure logged out primary")
		}
	}
}

func TestServicePathsAndRedirectsRejectUnregisteredTargets(t *testing.T) {
	config := campusServices["jwxk"]
	for _, path := range []string{"https://outside.example/xsxk/", "//outside.example/xsxk/", "/xsxk/../other", "/xsxk/%2e%2e/other", "/xsxk/%252e/other", "/other", "/xsxk/a#fragment", "/xsxk/a\\b"} {
		if _, err := servicePath(config, path); err == nil {
			t.Fatal(path)
		}
	}
	for _, raw := range []string{"https://outside.example/", "http://jwxk.neu.edu.cn/xsxk/", "https://pass.neu.edu.cn:444/tpass/login", "https://user@jwxk.neu.edu.cn/xsxk/"} {
		if _, err := serviceRedirect(raw, config, "direct"); err == nil {
			t.Fatal(raw)
		}
	}
	foreign, _ := WebVPNURL("https://jwxt.neu.edu.cn/private")
	if _, err := serviceRedirect(foreign, config, "webvpn"); err == nil {
		t.Fatal("another proxied service accepted")
	}
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		response := mockResponse(request, 302, "")
		response.Header.Set("Location", "https://outside.example/")
		return response, nil
	})
	if _, err := client.RequestService(context.Background(), ServiceRequest{Service: "jwxk", Method: "GET", Path: "/xsxk/web/now", ReadOnly: true}); err == nil || calls != 1 {
		t.Fatal("untrusted redirect followed")
	}
}

func TestServiceWebVPNVirtualTokenAndRouteIsolation(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	virtualReads, requests := 0, 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Host != vpnHost {
			t.Fatal("service escaped WebVPN route")
		}
		if request.URL.Path == "/wengine-vpn/cookie" {
			virtualReads++
			if request.URL.Query().Get("host") != "jwxk.neu.edu.cn" {
				t.Fatal("wrong virtual token host")
			}
			return mockResponse(request, 200, "token=virtual-session; other=value"), nil
		}
		decoded, err := RestoreServiceURL(request.URL.String(), "https://jwxk.neu.edu.cn")
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(decoded, "/auth/cas") {
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", "https://jwxk.neu.edu.cn/xsxk/profile/index.html")
			return response, nil
		}
		if strings.Contains(decoded, "/profile/index.html") {
			return mockResponse(request, 200, "home"), nil
		}
		requests++
		if request.Header.Get("Authorization") != "virtual-session" {
			t.Fatal("missing virtual token")
		}
		return mockResponse(request, 200, `{"code":200,"data":{}}`), nil
	})
	for attempt := 0; attempt < 2; attempt++ {
		if _, err := client.RequestService(context.Background(), ServiceRequest{Service: "jwxk", Mode: "webvpn", Method: "POST", Path: "/xsxk/web/now", ReadOnly: true}); err != nil {
			t.Fatal(err)
		}
	}
	if virtualReads != 1 || requests != 2 {
		t.Fatal(virtualReads, requests)
	}
	if active, mode := client.Status(); !active || mode != "direct" {
		t.Fatal("service route changed primary mode")
	}
}

func TestServiceCookieVerificationDoesNotRequirePrimaryOrSubmitCredentials(t *testing.T) {
	client, _ := NewClient("student-a")
	client.Restore(nil, "webvpn")
	requests := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		requests++
		if request.URL.Host != vpnHost || request.Method != "GET" {
			t.Fatal("cookie verification changed route or submitted a form")
		}
		if request.URL.Path == "/wengine-vpn/cookie" {
			if request.URL.Query().Get("host") != "jwxk.neu.edu.cn" || request.URL.Query().Get("method") == "set" {
				t.Fatal("verification cleared or crossed service cookies")
			}
			return mockResponse(request, 200, "token=restored-session"), nil
		}
		decoded, err := RestoreServiceURL(request.URL.String(), "https://jwxk.neu.edu.cn")
		if err != nil {
			t.Fatal("primary or unknown host queried", err)
		}
		if strings.Contains(decoded, "/auth/cas") {
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", "https://jwxk.neu.edu.cn/xsxk/profile/index.html")
			return response, nil
		}
		if strings.Contains(decoded, "/profile/index.html") {
			return mockResponse(request, 200, "home"), nil
		}
		t.Fatal("unexpected business request during verification")
		return nil, nil
	})
	if err := client.VerifyServiceSession(context.Background(), "jwxk", "webvpn"); err != nil || requests != 3 {
		t.Fatal(err, requests)
	}
	if err := client.VerifyServiceSession(context.Background(), "jwxk", "direct"); err == nil {
		t.Fatal("verification switched route")
	}
}

func TestServiceNotInRoundAndParentCookieAreNotAuthentication(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	target, _ := url.Parse("https://jwxt.neu.edu.cn/")
	client.jar.SetCookies(target, []*http.Cookie{{Name: "token", Value: "wrong-service", Domain: ".neu.edu.cn", Path: "/"}})
	if token, err := client.serviceToken(context.Background(), "jwxk", "direct", "/xsxk/web/now"); err != nil || token != "" {
		t.Fatal("parent-domain token leaked as bearer")
	}
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		return mockResponse(request, 200, "学生不在选课轮次中，暂时不能登录"), nil
	})
	_, err := client.RequestService(context.Background(), ServiceRequest{Service: "jwxk", Method: "POST", Path: "/xsxk/web/now", ReadOnly: true})
	if failure, ok := err.(*Error); !ok || failure.Code != "JWXK_NOT_IN_SELECTION_ROUND" || calls != 1 {
		t.Fatal(err)
	}
}

func TestServiceRateLimitSuppressesLaterProbesWithoutAuthentication(t *testing.T) {
	if serviceNeedsAuth("jwxk", "/xsxk/web/now", 401, []byte(`{"code":401,"msg":"请求频繁，请登录后再试"}`)) {
		t.Fatal("throttling misclassified as authentication")
	}
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	client.serviceReady = map[string]bool{"jwxk:direct": true}
	target, _ := url.Parse("https://jwxk.neu.edu.cn/xsxk/")
	client.jar.SetCookies(target, []*http.Cookie{{Name: "token", Value: "session", Path: "/xsxk/"}})
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		response := mockResponse(request, 200, `{"code":403,"msg":"请求频繁，请登录后重试"}`)
		response.Header.Set("Retry-After", "60")
		return response, nil
	})
	for attempt := 0; attempt < 2; attempt++ {
		_, err := client.RequestService(context.Background(), ServiceRequest{Service: "jwxk", Method: "POST", Path: "/xsxk/web/now", ReadOnly: true})
		if failure, ok := err.(*Error); !ok || failure.Code != "SERVICE_RATE_LIMIT" {
			t.Fatal(err)
		}
	}
	if calls != 1 || !client.serviceReady["jwxk:direct"] {
		t.Fatal("rate limit triggered network or authentication retry")
	}
}

func TestCXCYSessionUsesSharedJarWithoutJWXKToken(t *testing.T) {
	client, _ := NewClient("student-a")
	client.setStatus(true, "direct")
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		if request.URL.Host != "cxcy.neu.edu.cn" || request.Header.Get("Authorization") != "" {
			t.Fatal("wrong service destination or token")
		}
		if request.URL.Path == "/ucenter/auth/caslogin" {
			response := mockResponse(request, 302, "")
			response.Header.Set("Set-Cookie", "service-session=fixture; Path=/; Secure")
			response.Header.Set("Location", "/popscience/comp/ucenter/main/index")
			return response, nil
		}
		if cookie, err := request.Cookie("service-session"); err != nil || cookie.Value != "fixture" {
			t.Fatal("child session cookie lost")
		}
		return mockResponse(request, 200, "<html>已登录页面</html>"), nil
	})
	if _, err := client.RequestService(context.Background(), ServiceRequest{Service: "cxcy", Method: "GET", Path: "/popscience/comp/ucenter/main/index", ReadOnly: true}); err != nil {
		t.Fatal(err)
	}
	if calls != 3 {
		t.Fatal(calls)
	}
	client.Restore(nil, "direct")
	if len(client.serviceReady) != 0 || len(client.serviceTokens) != 0 {
		t.Fatal("restore retained prior child state")
	}
}

func TestWebVPNRefreshClearsOnlyRegisteredVirtualToken(t *testing.T) {
	client, _ := NewClient("student-a")
	client.serviceTokens = map[string]string{"jwxk:webvpn": "stale", "jwxk:direct": "keep"}
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		query := request.URL.Query()
		if request.Method != "POST" || request.URL.Host != vpnHost || request.URL.Path != "/wengine-vpn/cookie" || query.Get("host") != "jwxk.neu.edu.cn" || query.Get("method") != "set" || query.Get("ck_data") != "token=; Max-Age=0; Path=/" {
			t.Fatal("invalid virtual clear request")
		}
		return mockResponse(request, 200, "ok"), nil
	})
	if err := client.clearServiceToken(context.Background(), "jwxk", "webvpn"); err != nil {
		t.Fatal(err)
	}
	if client.serviceTokens["jwxk:webvpn"] != "" || client.serviceTokens["jwxk:direct"] != "keep" {
		t.Fatal("other route was cleared")
	}
}
