package campus

import (
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/publicsuffix"
)

type SavedCookie struct {
	URL    string      `json:"url"`
	Cookie http.Cookie `json:"cookie"`
}

type CookieJar struct {
	mutex   sync.Mutex
	jar     *cookiejar.Jar
	records map[string]SavedCookie
}

func NewCookieJar() *CookieJar {
	jar, _ := cookiejar.New(&cookiejar.Options{PublicSuffixList: publicsuffix.List})
	return &CookieJar{jar: jar, records: map[string]SavedCookie{}}
}

func (jar *CookieJar) Cookies(target *url.URL) []*http.Cookie { return jar.jar.Cookies(target) }

func (jar *CookieJar) SetCookies(target *url.URL, cookies []*http.Cookie) {
	jar.mutex.Lock()
	defer jar.mutex.Unlock()
	jar.jar.SetCookies(target, cookies)
	for _, source := range cookies {
		cookie := *source
		domain := strings.TrimPrefix(cookie.Domain, ".")
		if domain == "" {
			domain = target.Hostname()
		}
		if domain != target.Hostname() && !strings.HasSuffix(target.Hostname(), "."+domain) {
			continue
		}
		if cookie.Path == "" || !strings.HasPrefix(cookie.Path, "/") {
			cookie.Path = "/"
			if last := strings.LastIndex(target.Path, "/"); last > 0 {
				cookie.Path = target.Path[:last]
			}
		}
		key := domain + "\n" + cookie.Path + "\n" + cookie.Name
		if cookie.MaxAge < 0 || (!cookie.Expires.IsZero() && cookie.Expires.Before(time.Now())) {
			delete(jar.records, key)
			continue
		}
		if cookie.MaxAge > 0 {
			cookie.Expires = time.Now().Add(time.Duration(cookie.MaxAge) * time.Second)
			cookie.MaxAge = 0
		}
		if len(jar.records) >= 512 {
			if _, exists := jar.records[key]; !exists {
				continue
			}
		}
		jar.records[key] = SavedCookie{URL: target.Scheme + "://" + target.Host + cookie.Path, Cookie: cookie}
	}
}

func (jar *CookieJar) Snapshot() []SavedCookie {
	jar.mutex.Lock()
	defer jar.mutex.Unlock()
	result := make([]SavedCookie, 0, len(jar.records))
	for _, saved := range jar.records {
		if saved.Cookie.Expires.IsZero() || saved.Cookie.Expires.After(time.Now()) {
			result = append(result, saved)
		}
	}
	return result
}

func (client *Client) Snapshot() ([]SavedCookie, string) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	return client.jar.Snapshot(), client.mode
}

func (client *Client) Restore(ctxCookies []SavedCookie, mode string) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.jar = NewCookieJar()
	client.serviceReady, client.serviceTokens = nil, nil
	client.serviceCooldown = nil
	client.evaluationToken, client.evaluationExpiry = "", time.Time{}
	client.http.Jar = client.jar
	if mode != "webvpn" {
		mode = "direct"
	}
	client.setStatus(false, mode)
	for _, saved := range ctxCookies {
		target, err := url.Parse(saved.URL)
		if err == nil && trustedURL(target) {
			client.jar.SetCookies(target, []*http.Cookie{&saved.Cookie})
		}
	}
}
