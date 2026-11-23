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
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"golang.org/x/net/html"
	"golang.org/x/text/unicode/norm"
)

const casLogin = "https://pass.neu.edu.cn/tpass/login"
const primaryService = "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/index.do"
const loginScript = "https://pass.neu.edu.cn/tpass/comm/neu/js/login_neu.js"
const defaultPublicKey = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnjA28DLKXZzxbKmo9/1WkVLf1mr+wtLXLXt6sC4WiBCtsbzF5ewm7ARZeAdS3iZtqlYPn6IcUoOw42H8nAK/tfFcIb6dZ1K0atn0U39oWCGPzYuKtLJeMuNZiDXVuAXtojrckOjLW9B3gUnaNGLuIx0fYe66l0o9WjU2cGLNZQfiIxs2h00z1EA9IdSnVxiVQWSD+lsP3JZXh2TT287la4Y4603SQNKTK/QvXfcmccwTEd1IW6HwGxD6QrkInBiHisKWxmveN7UDSaQRZ/J97G0YC32pD38WT53izXeK0p/kU/X37VP555um1wVWFvPIuc9I7gMP1+hq5a+X6c++tQIDAQAB"

var publicKeyPattern = regexp.MustCompile(`(?:var|const|let)\s+publicKey(?:Str)?\s*=\s*["']([A-Za-z0-9+/=]+)["']`)

type Error struct {
	Code                   string
	Message                string
	ConfirmedServiceExpiry bool `json:"-"`
}

func (failure *Error) Error() string { return failure.Message }

type Client struct {
	mutex              sync.Mutex
	stateMutex         sync.RWMutex
	http               *http.Client
	jar                *CookieJar
	account            string
	mode               string
	loggedIn           bool
	evaluationToken    string
	evaluationExpiry   time.Time
	serviceReady       map[string]bool
	serviceTokens      map[string]string
	serviceCooldown    map[string]time.Time
	nextCatalogRequest time.Time
	authFlow           *vpnLoginFlow
	vpnTarget          string
	vpnSource          string
	vpnServiceState    string
	vpnGatewayVerified bool
}

func NormalizeAccount(account string) (string, error) {
	account = strings.TrimSpace(norm.NFKC.String(account))
	if account == "" || utf8.RuneCountInString(account) > 64 {
		return "", &Error{Code: "INVALID_ACCOUNT", Message: "请输入有效学号"}
	}
	for _, character := range account {
		if unicode.IsSpace(character) || unicode.IsControl(character) {
			return "", &Error{Code: "INVALID_ACCOUNT", Message: "学号不能包含空白或控制字符"}
		}
	}
	return account, nil
}

func NewClient(account string) (*Client, error) {
	normalized, err := NormalizeAccount(account)
	if err != nil {
		return nil, err
	}
	jar := NewCookieJar()
	client := &Client{account: normalized, mode: "direct", jar: jar}
	client.http = &http.Client{Jar: jar, Timeout: 25 * time.Second, CheckRedirect: func(request *http.Request, previous []*http.Request) error {
		if len(previous) >= 10 {
			return &Error{Code: "UPSTREAM_REDIRECT", Message: "学校登录跳转次数过多"}
		}
		if !trustedURL(request.URL) {
			return &Error{Code: "UNTRUSTED_REDIRECT", Message: "学校返回了未登记的跳转地址"}
		}
		if len(previous) > 0 && request.Method != http.MethodGet && request.Method != http.MethodHead {
			return &Error{Code: "UPSTREAM_REDIRECT", Message: "拒绝自动重放学校写请求，请核对操作结果"}
		}
		return nil
	}}
	return client, nil
}

func trustedURL(target *url.URL) bool {
	if target.User != nil || (target.Scheme != "https" && target.Scheme != "http") {
		return false
	}
	if port := target.Port(); port != "" && port != "443" && port != "80" {
		return false
	}
	switch strings.ToLower(target.Hostname()) {
	case "pass.neu.edu.cn", "jwxt.neu.edu.cn", "jwxk.neu.edu.cn", "cxcy.neu.edu.cn", vpnHost:
		return true
	}
	return false
}

func (client *Client) Account() string { return client.account }

func (client *Client) Status() (bool, string) {
	client.stateMutex.RLock()
	defer client.stateMutex.RUnlock()
	return client.loggedIn, client.mode
}

func (client *Client) setStatus(loggedIn bool, mode string) {
	client.stateMutex.Lock()
	defer client.stateMutex.Unlock()
	client.loggedIn, client.mode = loggedIn, mode
}

func (client *Client) Close() {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.setStatus(false, client.mode)
	client.evaluationToken = ""
	client.evaluationExpiry = time.Time{}
	client.serviceReady = nil
	client.serviceTokens = nil
	client.serviceCooldown = nil
	client.authFlow = nil
	client.vpnTarget, client.vpnSource, client.vpnServiceState = "", "", ""
	client.vpnGatewayVerified = false
	client.jar = NewCookieJar()
	client.http.Jar = client.jar
	client.http.CloseIdleConnections()
}

func (client *Client) send(ctx context.Context, method, raw string, form url.Values) ([]byte, *url.URL, error) {
	target, err := url.Parse(raw)
	if err != nil || !trustedURL(target) {
		return nil, nil, &Error{Code: "UNTRUSTED_URL", Message: "请求目标未登记"}
	}
	var body io.Reader
	if form != nil {
		body = strings.NewReader(form.Encode())
	}
	request, err := http.NewRequestWithContext(ctx, method, target.String(), body)
	if err != nil {
		return nil, nil, &Error{Code: "INVALID_REQUEST", Message: "无法创建学校请求"}
	}
	request.Header.Set("User-Agent", "Mozilla/5.0 NEU-JWXT-Toolkit")
	request.Header.Set("Accept-Language", "zh-CN,zh;q=0.9")
	if marker := strings.Index(target.Path, "/jwapp/sys/"); marker >= 0 {
		application := strings.Split(target.Path[marker+len("/jwapp/sys/"):], "/")[0]
		referer := *target
		referer.Path, referer.RawPath, referer.RawQuery, referer.Fragment = target.Path[:marker]+"/jwapp/sys/"+application+"/*default/index.do", "", "", ""
		request.Header.Set("Referer", referer.String())
		request.Header.Set("X-Requested-With", "XMLHttpRequest")
		request.Header.Set("Accept", "application/json, text/plain, */*")
	}
	if form != nil {
		request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	response, err := client.http.Do(request)
	if err != nil {
		return nil, nil, &Error{Code: "UPSTREAM_UNAVAILABLE", Message: "学校请求失败或超时，请检查网络；已发送操作不会自动重试"}
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, response.Request.URL, &Error{Code: "UPSTREAM_HTTP_ERROR", Message: "学校接口暂时不可用"}
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, 16*1024*1024+1))
	if err != nil || len(payload) > 16*1024*1024 {
		return nil, response.Request.URL, &Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校响应过大或未完整接收"}
	}
	return payload, response.Request.URL, nil
}

func loginFields(body []byte) (url.Values, string, string) {
	fields := url.Values{}
	key := ""
	if match := publicKeyPattern.FindSubmatch(body); len(match) > 1 {
		key = string(match[1])
	}
	document, err := html.Parse(strings.NewReader(string(body)))
	if err != nil {
		return fields, key, ""
	}
	message := ""
	var text func(*html.Node) string
	text = func(node *html.Node) string {
		if node.Type == html.TextNode {
			return node.Data
		}
		var result strings.Builder
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			result.WriteString(text(child))
		}
		return strings.TrimSpace(result.String())
	}
	var walk func(*html.Node)
	walk = func(node *html.Node) {
		attributes := map[string]string{}
		for _, attribute := range node.Attr {
			attributes[attribute.Key] = attribute.Val
		}
		if node.Type == html.ElementNode && node.Data == "input" {
			if attributes["type"] == "hidden" && attributes["name"] != "" {
				fields.Set(attributes["name"], attributes["value"])
			}
			if attributes["id"] == "publicKey" && len(attributes["value"]) > 100 {
				key = attributes["value"]
			}
		}
		classes := " " + attributes["class"] + " "
		if message == "" && (attributes["id"] == "errormsg" || attributes["id"] == "errormsghide" || strings.Contains(classes, " error ") || strings.Contains(classes, " alert ")) {
			message = text(node)
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(document)
	return fields, key, message
}

func passwordRejected(message string) bool {
	lower := strings.ToLower(message)
	for _, marker := range []string{"crypto", "rsa", "encrypt", "decrypt", "解密", "加密", "密文", "illegal"} {
		if strings.Contains(lower, marker) {
			return false
		}
	}
	for _, marker := range []string{"密码错误", "密码不正确", "密码有误", "用户名或密码", "账号或密码", "wrong password", "incorrect password", "password is incorrect", "invalid password", "账号不存在", "用户不存在", "学号不存在", "invalid username", "bad credentials", "account does not exist"} {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

func credentialForm(account, password, key string, hidden url.Values) (url.Values, error) {
	der, err := base64.StdEncoding.DecodeString(key)
	if err != nil {
		return nil, errors.New("invalid CAS public key")
	}
	parsed, err := x509.ParsePKIXPublicKey(der)
	if err != nil {
		parsed, err = x509.ParsePKCS1PublicKey(der)
	}
	if err != nil {
		return nil, errors.New("invalid CAS public key")
	}
	publicKey, ok := parsed.(*rsa.PublicKey)
	if !ok {
		return nil, errors.New("invalid CAS public key")
	}
	encrypted, err := rsa.EncryptPKCS1v15(rand.Reader, publicKey, []byte(account+password))
	if err != nil {
		return nil, errors.New("credentials exceed CAS encryption limit")
	}
	execution := hidden.Get("execution")
	if execution == "" {
		execution = "e1s1"
	}
	return url.Values{"un": {account}, "pd": {password}, "rsa": {base64.StdEncoding.EncodeToString(encrypted)},
		"ul": {strconv.Itoa(utf8.RuneCountInString(account))}, "pl": {strconv.Itoa(utf8.RuneCountInString(password))},
		"lt": {hidden.Get("lt")}, "execution": {execution}, "_eventId": {"submit"}}, nil
}

func (client *Client) LoginDirect(ctx context.Context, password string) error {
	if password == "" || utf8.RuneCountInString(password) > 256 {
		return &Error{Code: "INVALID_PASSWORD", Message: "请输入有效密码"}
	}
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.setStatus(false, "direct")
	if err := client.loginDirectTarget(ctx, password, primaryService, "jwxt.neu.edu.cn"); err != nil {
		return err
	}
	client.setStatus(true, "direct")
	return nil
}

func directCASResource(final *url.URL, expected string) bool {
	return final != nil && final.User == nil && final.Scheme+"://"+final.Host+final.EscapedPath() == expected
}

func (client *Client) loginDirectTarget(ctx context.Context, password, serviceURL, host string) error {
	loginURL := casLogin + "?" + url.Values{"service": {serviceURL}}.Encode()
	page, final, err := client.send(ctx, http.MethodGet, loginURL, nil)
	if err != nil {
		var failure *Error
		if errors.As(err, &failure) && failure.Code == "UPSTREAM_UNAVAILABLE" {
			return &Error{Code: "DIRECT_ACCESS_FAILED", Message: "校内直连不可达，请检查校园网络或使用WebVPN"}
		}
		return err
	}
	if final.Hostname() == vpnHost {
		return &Error{Code: "WEBVPN_REQUIRED", Message: "校外访问请切换WebVPN登录"}
	}
	if final.Hostname() == host {
		return nil
	}
	if !directCASResource(final, casLogin) {
		return serviceFailure("UNTRUSTED_REDIRECT", "学校统一认证页面地址不符")
	}
	hidden, key, _ := loginFields(page)
	if key == "" {
		key = defaultPublicKey
	}
	for attempt := 0; attempt < 2; attempt++ {
		form, err := credentialForm(client.account, password, key, hidden)
		if err != nil {
			return &Error{Code: "BAD_KEY", Message: "学校登录加密参数不可用"}
		}
		result, final, err := client.send(ctx, http.MethodPost, loginURL, form)
		if err != nil {
			return err
		}
		if final.Hostname() == vpnHost {
			return &Error{Code: "WEBVPN_REQUIRED", Message: "学校要求通过WebVPN登录"}
		}
		if final.Hostname() == host {
			return nil
		}
		_, _, message := loginFields(result)
		if passwordRejected(message) {
			return &Error{Code: "WRONG_PASSWORD", Message: "账号或密码错误"}
		}
		if message == "" || attempt > 0 {
			return &Error{Code: "LOGIN_REJECTED", Message: "学校认证未完成，请检查网络或切换WebVPN"}
		}
		script, source, err := client.send(ctx, http.MethodGet, loginScript+"?ts="+strconv.FormatInt(time.Now().Unix(), 10), nil)
		if err != nil {
			return err
		}
		if !directCASResource(source, loginScript) {
			return serviceFailure("UNTRUSTED_REDIRECT", "学校统一认证密钥地址不符")
		}
		_, refreshed, _ := loginFields(script)
		if refreshed == "" || refreshed == key {
			return &Error{Code: "LOGIN_REJECTED", Message: "学校拒绝登录，请稍后重试"}
		}
		key = refreshed
		client.jar = NewCookieJar()
		client.http.Jar = client.jar
		page, final, err = client.send(ctx, http.MethodGet, loginURL, nil)
		if err != nil {
			return err
		}
		if !directCASResource(final, casLogin) {
			return serviceFailure("UNTRUSTED_REDIRECT", "学校统一认证页面地址不符")
		}
		hidden, _, _ = loginFields(page)
	}
	return &Error{Code: "LOGIN_REJECTED", Message: "学校拒绝登录"}
}

func (client *Client) Request(ctx context.Context, method, target string, form url.Values) ([]byte, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if !client.loggedIn {
		return nil, &Error{Code: "AUTH_REQUIRED", Message: "请先登录"}
	}
	if client.mode == "webvpn" {
		converted, err := WebVPNURL(target)
		if err != nil {
			return nil, err
		}
		target = converted
	}
	payload, final, err := client.send(ctx, method, target, form)
	if err != nil {
		return nil, err
	}
	proxiedLogin := false
	if final.Hostname() == vpnHost {
		if restored, err := RestoreServiceURL(final.String(), "https://pass.neu.edu.cn"); err == nil {
			parsed, _ := url.Parse(restored)
			proxiedLogin = strings.HasPrefix(parsed.Path, "/tpass/")
		}
	}
	if final.Hostname() == "pass.neu.edu.cn" || proxiedLogin || (final.Hostname() == vpnHost && strings.HasPrefix(final.Path, "/login")) {
		client.setStatus(false, client.mode)
		return nil, &Error{Code: "AUTH_EXPIRED", Message: "学校登录已过期，请重新登录"}
	}
	return payload, nil
}
