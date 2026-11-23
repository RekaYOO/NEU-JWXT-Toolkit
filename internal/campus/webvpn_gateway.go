package campus

import (
	"net/http"
	"net/url"
	"strings"
)

func (client *Client) AdoptVPNGateway(account string, cookies []SavedCookie) error {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if account == "" || client.account != account {
		return vpnFailure("WEBVPN_ACCOUNT_MISMATCH", "认证账号与当前账号不一致")
	}
	jar := NewCookieJar()
	for _, saved := range client.jar.Snapshot() {
		target, err := url.Parse(saved.URL)
		if err == nil && target.Hostname() != vpnHost && strings.TrimPrefix(saved.Cookie.Domain, ".") != vpnHost {
			jar.SetCookies(target, []*http.Cookie{&saved.Cookie})
		}
	}
	for _, saved := range cookies {
		target, err := url.Parse(saved.URL)
		domain := strings.TrimPrefix(saved.Cookie.Domain, ".")
		if err == nil && target.Scheme == "https" && target.Hostname() == vpnHost && (domain == "" || domain == vpnHost) {
			jar.SetCookies(target, []*http.Cookie{&saved.Cookie})
		}
	}
	client.jar, client.http.Jar = jar, jar
	for key := range client.serviceTokens {
		if strings.HasSuffix(key, ":webvpn") {
			delete(client.serviceTokens, key)
		}
	}
	for key := range client.serviceReady {
		if strings.HasSuffix(key, ":webvpn") {
			delete(client.serviceReady, key)
		}
	}
	client.serviceCooldown = nil
	return nil
}
