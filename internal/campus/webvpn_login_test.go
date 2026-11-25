package campus

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestVPNPasswordSMSUsesOneSessionAndDoesNotResubmitVerifiedCode(t *testing.T) {
	client, _ := NewLoginClient("20250001")
	login, _ := WebVPNURL(casLogin)
	passwordPosts, smsSends, smsPosts, health := 0, 0, 0, 0
	ready := false
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path == "/login" {
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", login)
			return response, nil
		}
		if strings.HasSuffix(request.URL.Path, "/tpass/code") {
			if !strings.HasPrefix(request.URL.RawQuery, "vpn-1&") || request.Header.Get("Referer") != login {
				t.Fatal("wrong captcha endpoint")
			}
			response := mockResponse(request, 200, "GIF89a-test-image")
			response.Header.Set("Content-Type", "image/jpeg")
			return response, nil
		}
		if strings.HasSuffix(request.URL.Path, "/tpass/secondAuthCode") {
			request.ParseForm()
			smsSends++
			if request.URL.RawQuery != "vpn-12-o2-pass.neu.edu.cn" || len(request.PostForm) != 2 || request.PostForm.Get("code") != "manual-picture" || request.PostForm.Get("method") != "mobile" {
				t.Fatal("wrong SMS form", request.PostForm)
			}
			return mockResponse(request, 200, `{"info":"send"}`), nil
		}
		if strings.HasSuffix(request.URL.Path, "/tpass/login") {
			if request.Method == "GET" {
				response := mockResponse(request, 200, `<form id="loginForm"><input name="lt" type="hidden" value="ticket"><input name="execution" type="hidden" value="exec"></form>`)
				response.Header.Set("Set-Cookie", "candidate=bound; Path=/; Secure")
				return response, nil
			}
			if cookie, err := request.Cookie("candidate"); err != nil || cookie.Value != "bound" {
				t.Fatal("lost candidate cookies")
			}
			request.ParseForm()
			if request.PostForm.Get("scendAuthCode") == "" {
				passwordPosts++
				if request.PostForm.Get("rsa") == "" || request.PostForm.Get("un") != "20250001" {
					t.Fatal("missing CAS form")
				}
				return mockResponse(request, 200, `<form id="second_auth_form"><input name="execution" value="second"><input name="secret" value="not-public"></form>`), nil
			}
			smsPosts++
			if request.PostForm.Get("imgCode") != "manual-picture" || request.PostForm.Get("scendAuthCode") != "manual-sms" || request.PostForm.Get("execution") != "second" {
				t.Fatal("wrong verify form")
			}
			response := mockResponse(request, 302, "")
			response.Header.Set("Location", "https://webvpn.neu.edu.cn/portal")
			return response, nil
		}
		if strings.HasSuffix(request.URL.Path, "currentUser.do") {
			health++
			if ready {
				return mockResponse(request, 200, `{"code":"0","datas":{"userId":"20250001"}}`), nil
			}
			return mockResponse(request, 200, `{"code":1}`), nil
		}
		return mockResponse(request, 200, "gateway"), nil
	})
	ctx := context.Background()
	result, err := client.StartVPNPassword(ctx, "test-password")
	if err != nil || result["status"] != "sms_required" || smsSends != 0 || passwordPosts != 1 {
		t.Fatal(result, err)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "not-public") || !strings.Contains(string(encoded), "data:image/gif") {
		t.Fatal(string(encoded))
	}
	identifier := result["flow_id"].(string)
	if _, err := client.SendVPNSMS(ctx, identifier, "manual-picture"); err != nil {
		t.Fatal(err)
	}
	result, err = client.VerifyVPNSMS(ctx, identifier, "manual-sms")
	if err != nil || result["status"] != "session_pending" || smsPosts != 1 || health != 2 {
		t.Fatal(result, err, smsPosts, health)
	}
	expiry := client.authFlow.expires
	if client.authFlow.fields != nil || client.authFlow.captcha != "" {
		t.Fatal("retained one-time inputs")
	}
	result, err = client.VerifyVPNSMS(ctx, identifier, "")
	if err != nil || result["status"] != "session_pending" || !expiry.Equal(client.authFlow.expires) {
		t.Fatal(result, err)
	}
	ready = true
	result, err = client.VerifyVPNSMS(ctx, identifier, "")
	if err != nil || result["status"] != "authenticated" || smsPosts != 1 || smsSends != 1 || passwordPosts != 1 {
		t.Fatal(result, err)
	}
}

func TestDirectQRUsesPrimaryServiceAndChecksIdentity(t *testing.T) {
	for _, provider := range []string{QRSmartNEU, QRWeChat} {
		for _, scenario := range []string{"success", "mismatch", "gateway"} {
			t.Run(provider+"/"+scenario, func(t *testing.T) {
				client, err := NewLoginClient(map[string]string{"mismatch": "another-student"}[scenario])
				if err != nil {
					t.Fatal(err)
				}
				verified := false
				client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
					if request.URL.Hostname() == vpnHost {
						t.Fatal("direct login attempted to enter WebVPN")
					}
					switch request.URL.Path {
					case "/tpass/login":
						if request.URL.Query().Get("service") != primaryService {
							t.Fatal("incorrect service")
						}
					case "/tpass/qrLogin":
						if request.URL.Query().Get("action") == "create" {
							if request.URL.Query().Get("service") != primaryService {
								t.Fatal("incorrect app QR service")
							}
							return mockResponse(request, 200, `{"status":"ok","token":"fixture","qrUrl":"https://pass.neu.edu.cn/tpass/qrAuth?t=fixture","expiresIn":120}`), nil
						}
						fallthrough
					case "/tpass/checkQRCodeScan":
						return mockResponse(request, 200, `{"status":"ok","redirect_url":"/tpass/fixture-callback"}`), nil
					case "/tpass/fixture-callback":
						response := mockResponse(request, 302, "")
						location := primaryService + "?ticket=fixture"
						if scenario == "gateway" {
							location = vpnEntry
						}
						response.Header.Set("Location", location)
						return response, nil
					case "/jwapp/sys/homeapp/index.do":
						response := mockResponse(request, 200, "school home")
						response.Header.Set("Set-Cookie", "SESSION=fixture; Path=/; Secure; HttpOnly")
						return response, nil
					case "/jwapp/sys/homeapp/api/home/currentUser.do":
						if cookie, err := request.Cookie("SESSION"); err != nil || cookie.Value != "fixture" {
							t.Fatal("missing school session cookie")
						}
						verified = true
						return mockResponse(request, 200, `{"code":"0","datas":{"userId":"student"}}`), nil
					default:
						t.Fatalf("unexpected path %s", request.URL.Path)
					}
					return mockResponse(request, 200, "CAS login"), nil
				})
				result, err := client.StartQR(context.Background(), provider, "direct")
				if err != nil || result["network_mode"] != "direct" {
					t.Fatal(result, err)
				}
				if provider == QRWeChat && !strings.Contains(result["qr_content"].(string), "&service="+primaryService) {
					t.Fatal("incorrect WeChat QR service")
				}
				result, err = client.PollVPNQR(context.Background(), result["flow_id"].(string))
				active, mode := client.Status()
				if scenario == "success" {
					if err != nil || !verified || !active || mode != "direct" || result["username"] != "student" {
						t.Fatal(result, err, active, mode)
					}
					return
				}
				if err == nil || active || verified != (scenario == "mismatch") {
					t.Fatal(result, err, active, verified)
				}
			})
		}
	}
}

func TestVPNQRIdentityAndUntrustedCallback(t *testing.T) {
	for _, scenario := range []string{"success", "mismatch", "foreign", "proxy-foreign"} {
		t.Run(scenario, func(t *testing.T) {
			hint := ""
			if scenario == "mismatch" {
				hint = "expected"
			}
			client, _ := NewLoginClient(hint)
			polls := 0
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				if request.URL.Path == "/tpass/checkQRCodeScan" {
					polls++
					if request.URL.Query().Get("uuid") == "" {
						t.Fatal("missing QR uuid")
					}
					callback := "/tpass/callback"
					if scenario == "foreign" {
						callback = "https://example.invalid/steal"
					}
					if scenario == "proxy-foreign" {
						callback, _ = WebVPNURL("https://example.invalid/steal")
					}
					payload, _ := json.Marshal(map[string]string{"redirect_url": callback})
					return mockResponse(request, 200, string(payload)), nil
				}
				if strings.HasSuffix(request.URL.Path, "currentUser.do") {
					return mockResponse(request, 200, `{"code":0,"datas":{"userId":"actual"}}`), nil
				}
				return mockResponse(request, 200, "page"), nil
			})
			result, err := client.StartVPNQR(context.Background(), QRWeChat)
			if err != nil {
				t.Fatal(err)
			}
			content, _ := url.Parse(result["qr_content"].(string))
			if content.Host != "pass.neu.edu.cn" || !strings.Contains(content.RawQuery, "service="+vpnEntry) {
				t.Fatal(content)
			}
			result, err = client.PollVPNQR(context.Background(), result["flow_id"].(string))
			if scenario == "success" {
				if err != nil || result["username"] != "actual" {
					t.Fatal(result, err)
				}
				return
			}
			var failure *Error
			if !errors.As(err, &failure) {
				t.Fatal("unsafe callback accepted", result, err)
			}
			if scenario == "mismatch" && failure.Code != "WEBVPN_ACCOUNT_MISMATCH" {
				t.Fatal(failure)
			}
			if scenario != "mismatch" && failure.Code != "WEBVPN_UPSTREAM_REDIRECT" {
				t.Fatal(failure)
			}
			if loggedIn, _ := client.Status(); loggedIn || polls != 1 {
				t.Fatal("false authenticated state")
			}
		})
	}
}

func TestVPNFlowExpiryAndWriteRedirectDoNotReplay(t *testing.T) {
	client, _ := NewLoginClient("student")
	client.authFlow = &vpnLoginFlow{id: "expired", kind: "sms", expires: time.Now().Add(-time.Second)}
	if _, err := client.VerifyVPNSMS(context.Background(), "expired", "code"); err == nil {
		t.Fatal("expired flow accepted")
	}
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		response := mockResponse(request, 307, "")
		response.Header.Set("Location", casLogin)
		return response, nil
	})
	if _, _, err := client.vpnSend(context.Background(), "POST", casLogin, url.Values{"code": {"test"}}, ""); err == nil || calls != 1 {
		t.Fatal("authentication write replayed", calls, err)
	}
}

func TestVPNFollowsOnlyWrappedGatewayLoginCallbackAfterPassword(t *testing.T) {
	wrapper := "https://" + vpnHost + "/https/" + vpnPrefix + encryptedHostname(vpnHost)
	callbackQuery := "?cas_login=true&ticket=ST-test"
	gatewayToken := wrapper + "/wengine-vpn-token-login?token=first"
	loginToken := "https://" + vpnHost + "/token-login?token=second"
	for _, scenario := range []struct {
		name, location string
		status         int
		accepted       bool
	}{
		{"gateway callback", wrapper + "/login" + callbackQuery, 302, true},
		{"no ticket", wrapper + "/login?cas_login=true", 302, false},
		{"ambiguous ticket", wrapper + "/login" + callbackQuery + "&ticket=ST-other", 302, false},
		{"wrong path", wrapper + "/logout" + callbackQuery, 302, false},
		{"skipped callback", gatewayToken, 302, false},
		{"encoded path", wrapper + "/%6cogin" + callbackQuery, 302, false},
		{"foreign wrapped host", "https://" + vpnHost + "/https/" + vpnPrefix + encryptedHostname("example.net") + "/login" + callbackQuery, 302, false},
		{"form replay", wrapper + "/login" + callbackQuery, 307, false},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			client, _ := NewLoginClient("20250001")
			login, _ := WebVPNURL(casLogin)
			primaryPage, _ := WebVPNURL(primaryService)
			passwordPosts, callbackReads := 0, 0
			var businessReady bool
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				switch {
				case request.URL.String() == vpnEntry:
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", login)
					return response, nil
				case request.URL.String() == login && request.Method == http.MethodGet:
					return mockResponse(request, 200, `<form id="loginForm"><input name="execution" value="exec"></form>`), nil
				case request.URL.String() == login && request.Method == http.MethodPost:
					passwordPosts++
					response := mockResponse(request, scenario.status, "")
					response.Header.Set("Location", scenario.location)
					return response, nil
				case request.URL.String() == wrapper+"/login"+callbackQuery && request.Method == http.MethodGet:
					callbackReads++
					if request.Body != nil || request.Header.Get("X-Requested-With") != "" {
						t.Fatal("callback must be a navigation without a replayed form")
					}
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", gatewayToken)
					return response, nil
				case request.URL.String() == gatewayToken && request.Method == http.MethodGet:
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", loginToken)
					return response, nil
				case request.URL.String() == loginToken && request.Method == http.MethodGet:
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", "https://"+vpnHost+"/")
					return response, nil
				case request.URL.Path == "/":
					return mockResponse(request, 200, "gateway"), nil
				case request.URL.String() == primaryPage && request.Method == http.MethodGet:
					if request.Header.Get("X-Requested-With") != "" {
						t.Fatal("school entry must be a page navigation")
					}
					businessReady = true
					return mockResponse(request, 200, "school home"), nil
				case strings.HasSuffix(request.URL.Path, "/currentUser.do"):
					if !businessReady {
						return mockResponse(request, 401, "session not established"), nil
					}
					return mockResponse(request, 200, `{"code":"0","datas":{"userId":"20250001"}}`), nil
				default:
					t.Fatalf("unexpected request %s %s", request.Method, request.URL)
					return nil, nil
				}
			})
			result, err := client.StartVPNPassword(context.Background(), "test-password")
			if passwordPosts != 1 {
				t.Fatalf("expected one form submission, got %d", passwordPosts)
			}
			if scenario.accepted {
				if err != nil || result["status"] != "authenticated" || callbackReads != 1 {
					t.Fatal(result, err, callbackReads)
				}
				return
			}
			var failure *Error
			if !errors.As(err, &failure) || failure.Code != "WEBVPN_UPSTREAM_REDIRECT" || callbackReads != 0 {
				t.Fatal(result, err, callbackReads)
			}
		})
	}
}

func TestVPNGatewayMergePreservesPrimaryAndDirectServiceState(t *testing.T) {
	client, _ := NewClient("student")
	client.setStatus(true, "direct")
	primary, _ := url.Parse("https://jwxt.neu.edu.cn/")
	gateway, _ := url.Parse("https://webvpn.neu.edu.cn/")
	client.jar.SetCookies(primary, []*http.Cookie{{Name: "primary", Value: "keep", Path: "/"}})
	client.jar.SetCookies(gateway, []*http.Cookie{{Name: "obsolete", Value: "remove", Path: "/"}})
	client.serviceTokens = map[string]string{"jwxk:direct": "keep", "jwxk:webvpn": "invalidate"}
	cookies := []SavedCookie{{URL: gateway.String(), Cookie: http.Cookie{Name: "gateway", Value: "new", Path: "/"}}, {URL: primary.String(), Cookie: http.Cookie{Name: "primary", Value: "wrong", Path: "/"}}}
	if err := client.AdoptVPNGateway("other", cookies); err == nil {
		t.Fatal("foreign account merged")
	}
	if err := client.AdoptVPNGateway("student", cookies); err != nil {
		t.Fatal(err)
	}
	if active, mode := client.Status(); !active || mode != "direct" {
		t.Fatal(active, mode)
	}
	if client.jar.Cookies(primary)[0].Value != "keep" {
		t.Fatal("primary cookies overwritten")
	}
	if tokens := client.jar.Cookies(gateway); len(tokens) != 1 || tokens[0].Name != "gateway" {
		t.Fatal("gateway cookies not replaced")
	}
	if client.serviceTokens["jwxk:direct"] != "keep" || client.serviceTokens["jwxk:webvpn"] != "" {
		t.Fatal("service cache isolation failed")
	}
}

func TestVPNSavedCookiesRequireOfficialIdentityWithoutSubmittingPassword(t *testing.T) {
	client, _ := NewClient("student")
	client.Restore([]SavedCookie{{URL: "https://webvpn.neu.edu.cn/", Cookie: http.Cookie{Name: "gateway", Value: "test-only", Path: "/", Secure: true}}}, "webvpn")
	calls := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		if !strings.HasSuffix(request.URL.Path, "currentUser.do") {
			t.Fatal("restoration submitted credentials")
		}
		if cookie, err := request.Cookie("gateway"); err != nil || cookie.Value != "test-only" {
			t.Fatal("saved gateway cookie missing")
		}
		return mockResponse(request, 200, `{"code":0,"datas":{"userId":"student"}}`), nil
	})
	if err := client.VerifyVPN(context.Background()); err != nil {
		t.Fatal(err)
	}
	if active, mode := client.Status(); !active || mode != "webvpn" || calls != 1 {
		t.Fatal(active, mode, calls)
	}
}

func TestVPNBusinessRedirectToProxiedCASExpiresSession(t *testing.T) {
	client, _ := NewClient("student")
	client.setStatus(true, "webvpn")
	login, _ := WebVPNURL(casLogin)
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		if strings.HasSuffix(request.URL.Path, "/tpass/login") {
			return mockResponse(request, 200, `<form id="loginForm"></form>`), nil
		}
		response := mockResponse(request, 302, "")
		response.Header.Set("Location", login)
		return response, nil
	})
	_, err := client.Request(context.Background(), "GET", "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/index.do", nil)
	var failure *Error
	if !errors.As(err, &failure) || failure.Code != "AUTH_EXPIRED" {
		t.Fatal(err)
	}
	if active, _ := client.Status(); active {
		t.Fatal("expired VPN session remained active")
	}
}

func TestVPNPasswordServiceRecoveryDoesNotDependOnPrimaryAvailability(t *testing.T) {
	for _, scenario := range []struct{ service, state string }{{"jwxk", "authenticated"}, {"jwxk", "not_in_selection_round"}, {"jwxk", "service_unavailable"}, {"jwxk", "login_required"}, {"cxcy", "authenticated"}, {"cxcy", "service_unavailable"}, {"cxcy", "login_required"}} {
		t.Run(scenario.service+"-"+scenario.state, func(t *testing.T) {
			client, _ := NewLoginClient("student")
			if err := client.SetVPNTarget(scenario.service); err != nil {
				t.Fatal(err)
			}
			login, _ := WebVPNURL(casLogin)
			serviceRequests := 0
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				if request.URL.Path == "/login" {
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", login)
					return response, nil
				}
				if strings.HasSuffix(request.URL.Path, "/tpass/login") {
					if request.Method == "GET" {
						return mockResponse(request, 200, `<form id="loginForm"><input type="hidden" name="execution" value="one"></form>`), nil
					}
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", "https://webvpn.neu.edu.cn/portal")
					return response, nil
				}
				if request.URL.Path == "/portal" {
					return mockResponse(request, 200, "gateway authenticated"), nil
				}
				if strings.Contains(request.URL.Path, "/jwapp/") {
					t.Fatal("service password recovery queried unavailable primary")
				}
				if request.URL.Path == "/wengine-vpn/cookie" {
					return mockResponse(request, 200, "token=test-only-token"), nil
				}
				origin := "https://" + campusServices[scenario.service].host
				decoded, err := RestoreServiceURL(request.URL.String(), origin)
				if err != nil {
					t.Fatal("unexpected service destination", err)
				}
				serviceRequests++
				if scenario.state == "not_in_selection_round" {
					return mockResponse(request, 200, "学生不在选课轮次中，暂时不能登录"), nil
				}
				if scenario.state == "service_unavailable" {
					return mockResponse(request, 503, "unavailable"), nil
				}
				if scenario.state == "login_required" {
					return mockResponse(request, 401, "login required"), nil
				}
				if strings.Contains(decoded, "/auth/cas") {
					response := mockResponse(request, 302, "")
					response.Header.Set("Location", origin+"/profile/index.html")
					return response, nil
				}
				return mockResponse(request, 200, "service profile"), nil
			})
			result, err := client.StartVPNPassword(context.Background(), "test-password")
			if err != nil || result["status"] != "authenticated" || result["service_auth_state"] != scenario.state || result["target_service"] != scenario.service || serviceRequests == 0 {
				t.Fatal(result, err, serviceRequests)
			}
		})
	}
}

func TestVPNServiceQRCodeStillRequiresVerifiedAccount(t *testing.T) {
	client, _ := NewLoginClient("student")
	client.SetVPNTarget("jwxk")
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path == "/tpass/checkQRCodeScan" {
			return mockResponse(request, 200, `{"redirect_url":"https://webvpn.neu.edu.cn/portal"}`), nil
		}
		if _, err := RestoreServiceURL(request.URL.String(), "https://jwxk.neu.edu.cn"); err == nil {
			t.Fatal("unverified QR account reached target service")
		}
		if strings.HasSuffix(request.URL.Path, "currentUser.do") {
			return mockResponse(request, 503, "unavailable"), nil
		}
		return mockResponse(request, 200, "page"), nil
	})
	result, err := client.StartVPNQR(context.Background(), QRWeChat)
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.PollVPNQR(context.Background(), result["flow_id"].(string))
	var failure *Error
	if !errors.As(err, &failure) || failure.Code != "WEBVPN_ACCOUNT_UNVERIFIED" {
		t.Fatal(err)
	}
	if active, _ := client.Status(); active {
		t.Fatal("unverified QR authenticated")
	}
}
