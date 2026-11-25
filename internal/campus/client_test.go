package campus

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

type transportFunc func(*http.Request) (*http.Response, error)

func TestPostRedirectCannotReplayWrite(t *testing.T) {
	for _, status := range []int{307, 308} {
		client, err := NewClient("20250001")
		if err != nil {
			t.Fatal(err)
		}
		calls := 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			calls++
			response := mockResponse(request, status, "")
			response.Header.Set("Location", "https://jwxt.neu.edu.cn/redirected")
			return response, nil
		})
		_, _, err = client.send(context.Background(), http.MethodPost, "https://jwxt.neu.edu.cn/write", url.Values{"WID": {"test"}})
		if err == nil || calls != 1 {
			t.Fatalf("write replayed: status=%d calls=%d err=%v", status, calls, err)
		}
	}
}

func (transport transportFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return transport(request)
}

func mockResponse(request *http.Request, status int, body string) *http.Response {
	return &http.Response{Request: request, StatusCode: status, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body))}
}

func TestDirectLoginOfficialFormAndCookieContinuity(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, _ := x509.MarshalPKIXPublicKey(&key.PublicKey)
	publicKey := base64.StdEncoding.EncodeToString(der)
	client, err := NewClient(" ２０２５０００１ ")
	if err != nil {
		t.Fatal(err)
	}
	posts := 0
	client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Hostname() == "jwxt.neu.edu.cn" {
			return mockResponse(request, 200, "school home"), nil
		}
		if request.Method == "GET" {
			response := mockResponse(request, 200, `<input type="hidden" name="lt" value="one-use"><input type="hidden" name="execution" value="e2s1"><script>const publicKeyStr = "`+publicKey+`";</script>`)
			response.Header.Set("Set-Cookie", "SESSION=login-candidate; Path=/; Secure; HttpOnly")
			return response, nil
		}
		posts++
		if cookie, err := request.Cookie("SESSION"); err != nil || cookie.Value != "login-candidate" {
			t.Fatal("lost CAS candidate cookie")
		}
		if err := request.ParseForm(); err != nil {
			t.Fatal(err)
		}
		if request.Form.Get("un") != "20250001" || request.Form.Get("pd") != "test-password" || request.Form.Get("lt") != "one-use" || request.Form.Get("execution") != "e2s1" {
			t.Fatal("official form mismatch")
		}
		ciphertext, _ := base64.StdEncoding.DecodeString(request.Form.Get("rsa"))
		plaintext, err := rsa.DecryptPKCS1v15(rand.Reader, key, ciphertext)
		if err != nil || string(plaintext) != "20250001test-password" {
			t.Fatal("RSA payload differs from official protocol")
		}
		response := mockResponse(request, 302, "")
		response.Header.Set("Location", primaryService)
		return response, nil
	})
	if err := client.LoginDirect(context.Background(), "test-password"); err != nil {
		t.Fatal(err)
	}
	if loggedIn, mode := client.Status(); !loggedIn || mode != "direct" || posts != 1 {
		t.Fatal("login not confirmed")
	}
}

func TestDirectLoginDoesNotReplayLostResponseOrWrongPassword(t *testing.T) {
	for _, lost := range []bool{false, true} {
		client, _ := NewClient("student")
		posts, requests := 0, 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			if request.URL.String() == primaryService {
				return mockResponse(request, 200, "school home"), nil
			}
			requests++
			if request.Method == "GET" {
				return mockResponse(request, 200, `<input type="hidden" name="lt" value="token">`), nil
			}
			posts++
			if lost {
				return nil, errors.New("connection lost with private details")
			}
			return mockResponse(request, 200, `<div id="errormsg">用户名或密码错误</div>`), nil
		})
		err := client.LoginDirect(context.Background(), "private-password")
		if err == nil || posts != 1 || requests != 2 {
			t.Fatalf("login replayed or accepted: %d %d %v", posts, requests, err)
		}
		if strings.Contains(err.Error(), "private") {
			t.Fatal("sensitive error escaped")
		}
	}
}

func TestCookieSnapshotPreservesPathAndDeletion(t *testing.T) {
	jar := NewCookieJar()
	target, _ := url.Parse("https://pass.neu.edu.cn/tpass/login")
	jar.SetCookies(target, []*http.Cookie{{Name: "one", Value: "session", Secure: true}, {Name: "two", Value: "persistent", MaxAge: 60}})
	snapshot := jar.Snapshot()
	if len(snapshot) != 2 {
		t.Fatal("missing cookies")
	}
	client, _ := NewClient("student")
	client.Restore(snapshot, "direct")
	if len(client.jar.Cookies(target)) != 2 {
		t.Fatal("cookie restore failed")
	}
	if loggedIn, _ := client.Status(); loggedIn {
		t.Fatal("stored cookie was treated as verified identity")
	}
	jar.SetCookies(target, []*http.Cookie{{Name: "one", MaxAge: -1}})
	if len(jar.Snapshot()) != 1 {
		t.Fatal("deleted cookie persisted")
	}
}
