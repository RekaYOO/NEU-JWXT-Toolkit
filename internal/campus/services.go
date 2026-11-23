package campus

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

type ServiceRequest struct {
	Service               string
	Mode                  string
	Method                string
	Path                  string
	Form                  url.Values
	JSON                  json.RawMessage
	ReadOnly              bool
	BatchID               string
	RefreshSession        bool
	PreserveSessionOnAuth bool
}

type serviceConfig struct {
	host       string
	callback   string
	prefixes   []string
	loginPaths []string
}

var campusServices = map[string]serviceConfig{
	"jwxk": {host: "jwxk.neu.edu.cn", callback: "/xsxk/auth/cas", prefixes: []string{"/xsxk/"}, loginPaths: []string{"/xsxk/auth/cas"}},
	"cxcy": {host: "cxcy.neu.edu.cn", callback: "/ucenter/auth/caslogin?type=student", prefixes: []string{"/popscience/comp/ucenter/", "/originality/comp/ucenter/", "/technical/comp/ucenter/", "/business/comp/ucenter/", "/popscience/comp/front/comp/info", "/originality/comp/front/comp/info", "/technical/comp/front/comp/info", "/business/comp/front/comp/info", "/static/uploads/"}, loginPaths: []string{"/ucenter/index/login", "/ucenter/auth/cas", "/ucenter/auth/caslogin"}},
}

func serviceFailure(code, message string) error { return &Error{Code: code, Message: message} }

func servicePath(config serviceConfig, path string) (*url.URL, error) {
	parsed, err := url.Parse(path)
	if err != nil || parsed.IsAbs() || parsed.Host != "" || parsed.Fragment != "" || !strings.HasPrefix(path, "/") || strings.ContainsAny(parsed.Path, "\\%\r\n\x00") {
		return nil, serviceFailure("INVALID_REQUEST", "子服务请求路径无效")
	}
	for _, segment := range strings.Split(parsed.Path, "/") {
		if segment == "." || segment == ".." {
			return nil, serviceFailure("INVALID_REQUEST", "子服务请求路径无效")
		}
	}
	for _, prefix := range config.prefixes {
		if strings.HasPrefix(parsed.Path, prefix) {
			return parsed, nil
		}
	}
	return nil, serviceFailure("INVALID_REQUEST", "子服务接口未登记")
}

func routeServiceURL(raw, mode string) (string, error) {
	if mode == "webvpn" {
		return WebVPNURL(raw)
	}
	return raw, nil
}

func serviceRedirect(raw string, config serviceConfig, mode string) (string, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.User != nil || parsed.Fragment != "" {
		return "", serviceFailure("UNTRUSTED_REDIRECT", "子服务跳转地址无效")
	}
	if parsed.Scheme == "http" && parsed.Hostname() == "pass.neu.edu.cn" && (parsed.Port() == "" || parsed.Port() == "80") && parsed.Path == "/tpass/login" {
		parsed.Scheme = "https"
		parsed.Host = "pass.neu.edu.cn"
	}
	if mode == "webvpn" && parsed.Host == vpnHost {
		for _, source := range []string{"http://pass.neu.edu.cn/tpass/login", "http://pass.neu.edu.cn:80/tpass/login"} {
			proxy, _ := WebVPNURL(source)
			expected, _ := url.Parse(proxy)
			if parsed.Path == expected.Path {
				secure, _ := WebVPNURL(casLogin)
				replacement, _ := url.Parse(secure)
				parsed.Path, parsed.RawPath = replacement.Path, ""
			}
		}
		for _, host := range []string{config.host, "pass.neu.edu.cn"} {
			if restored, err := RestoreServiceURL(parsed.String(), "https://"+host); err == nil {
				return routeServiceURL(restored, mode)
			}
		}
		return "", serviceFailure("UNTRUSTED_REDIRECT", "子服务返回未登记的代理地址")
	}
	if parsed.Scheme != "https" || (parsed.Port() != "" && parsed.Port() != "443") || (parsed.Hostname() != config.host && parsed.Hostname() != "pass.neu.edu.cn") {
		return "", serviceFailure("UNTRUSTED_REDIRECT", "子服务返回未登记的跳转地址")
	}
	return routeServiceURL(parsed.String(), mode)
}

func (client *Client) serviceExchange(ctx context.Context, method, target string, body []byte, headers http.Header) ([]byte, http.Header, int, error) {
	return client.serviceExchangeBounded(ctx, method, target, body, headers, 16*1024*1024)
}

func (client *Client) serviceExchangeBounded(ctx context.Context, method, target string, body []byte, headers http.Header, maximum int64) ([]byte, http.Header, int, error) {
	request, err := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(body))
	if err != nil {
		return nil, nil, 0, serviceFailure("INVALID_REQUEST", "无法创建子服务请求")
	}
	request.Header = headers.Clone()
	request.Header.Set("User-Agent", "Mozilla/5.0 NEU-JWXT-Toolkit")
	transport := *client.http
	transport.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := transport.Do(request)
	if err != nil {
		return nil, nil, 0, serviceFailure("UPSTREAM_UNAVAILABLE", "子服务请求失败或超时，已发送操作不会自动重试")
	}
	defer response.Body.Close()
	if response.ContentLength > maximum {
		return nil, nil, 0, serviceFailure("UPSTREAM_RESPONSE_INVALID", "子服务响应超过大小限制")
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, maximum+1))
	if err != nil || int64(len(payload)) > maximum {
		return nil, nil, 0, serviceFailure("UPSTREAM_RESPONSE_INVALID", "子服务响应过大或未完整接收")
	}
	return payload, response.Header, response.StatusCode, nil
}

func serviceAccess(payload []byte) error {
	text := strings.Join(strings.Fields(string(payload)), "")
	if strings.Contains(text, "学生不在选课轮次中") && strings.Contains(text, "暂时不能登录") {
		return serviceFailure("JWXK_NOT_IN_SELECTION_ROUND", "当前账号不在学校开放的选课轮次中，暂时不能进入选课系统")
	}
	if strings.Contains(text, "校园网") && (strings.Contains(text, "无需使用") || strings.Contains(text, "不允许访问")) {
		return serviceFailure("WEBVPN_CAMPUS_NETWORK", "当前网络无法使用WebVPN，请选择直连")
	}
	return nil
}

func serviceNeedsAuth(service, path string, status int, payload []byte) bool {
	if status == http.StatusTooManyRequests || status >= http.StatusInternalServerError {
		return false
	}
	if status >= 300 && status < 400 {
		return true
	}
	lower := strings.ToLower(strings.TrimSpace(string(payload)))
	if service == "cxcy" {
		return status == 401 || strings.Contains(lower, "统一身份认证") && strings.Contains(lower, "password")
	}
	var result map[string]any
	if json.Unmarshal(payload, &result) == nil {
		code := fmt.Sprint(result["code"])
		message := strings.ToLower(fmt.Sprint(result["msg"], result["message"]))
		for _, marker := range []string{"请求过快", "请求频繁", "访问频繁", "操作频繁"} {
			if strings.Contains(message, marker) {
				return false
			}
		}
		if code == "401" || code == "402" || code == "403" {
			for _, marker := range []string{"登录", "认证", "未授权", "授权失效", "会话", "token", "令牌"} {
				if strings.Contains(message, marker) {
					return true
				}
			}
		}
	}
	jsonPath := strings.HasPrefix(path, "/xsxk/elective/") || strings.HasPrefix(path, "/xsxk/volunteer/") || path == "/xsxk/web/now" || path == "/xsxk/web/studentInfo"
	return status == 401 || jsonPath && (strings.HasPrefix(lower, "<!doctype html") || strings.HasPrefix(lower, "<html"))
}

func serviceRateLimit(payload []byte, headers http.Header, status int) time.Duration {
	var result map[string]any
	json.Unmarshal(payload, &result)
	code, message := fmt.Sprint(result["code"]), fmt.Sprint(result["msg"], result["message"])
	limited := status == 429
	if status == http.StatusUnauthorized || code == "401" || code == "403" || code == "429" {
		for _, marker := range []string{"请求过快", "请求频繁", "访问频繁", "操作频繁"} {
			if strings.Contains(message, marker) {
				limited = true
			}
		}
	}
	if !limited {
		return 0
	}
	seconds, _ := strconv.Atoi(headers.Get("Retry-After"))
	if seconds < 30 {
		seconds = 30
	}
	if seconds > 300 {
		seconds = 300
	}
	return time.Duration(seconds) * time.Second
}

func (client *Client) serviceToken(ctx context.Context, service, mode, path string) (string, error) {
	if service != "jwxk" {
		return "", nil
	}
	config := campusServices[service]
	if mode == "direct" {
		target, _ := url.Parse("https://" + config.host + path)
		token, bestPath := "", -1
		for _, saved := range client.jar.Snapshot() {
			source, err := url.Parse(saved.URL)
			if err != nil {
				continue
			}
			domain := strings.TrimPrefix(saved.Cookie.Domain, ".")
			if domain == "" {
				domain = source.Hostname()
			}
			if domain != config.host || saved.Cookie.Name != "token" {
				continue
			}
			cookiePath := saved.Cookie.Path
			if cookiePath == "" {
				cookiePath = "/"
			}
			matches := path == cookiePath || strings.HasPrefix(path, strings.TrimSuffix(cookiePath, "/")+"/")
			if !matches || len(cookiePath) <= bestPath {
				continue
			}
			for _, cookie := range client.jar.Cookies(target) {
				if cookie.Name == "token" && cookie.Value == saved.Cookie.Value {
					token, bestPath = cookie.Value, len(cookiePath)
				}
			}
		}
		return token, nil
	}
	key := service + ":" + mode
	if token := client.serviceTokens[key]; token != "" {
		return token, nil
	}
	query := url.Values{"method": {"get"}, "host": {config.host}, "scheme": {"https"}, "path": {path}, "vpn_timestamp": {fmt.Sprint(time.Now().UnixMilli())}}
	payload, _, status, err := client.serviceExchange(ctx, http.MethodGet, "https://"+vpnHost+"/wengine-vpn/cookie?"+query.Encode(), nil, http.Header{"X-Requested-With": {"XMLHttpRequest"}})
	if err != nil {
		return "", err
	}
	if status != 200 {
		return "", serviceFailure("SERVICE_AUTH_REQUIRED", "请完成该服务的WebVPN登录")
	}
	request := &http.Request{Header: http.Header{"Cookie": {string(payload)}}}
	cookie, err := request.Cookie("token")
	if err != nil {
		return "", nil
	}
	if client.serviceTokens == nil {
		client.serviceTokens = map[string]string{}
	}
	client.serviceTokens[key] = cookie.Value
	return cookie.Value, nil
}

func (client *Client) clearServiceToken(ctx context.Context, service, mode string) error {
	delete(client.serviceTokens, service+":"+mode)
	if service != "jwxk" {
		return nil
	}
	if mode == "webvpn" {
		query := url.Values{"method": {"set"}, "host": {campusServices[service].host}, "scheme": {"https"}, "path": {"/xsxk/auth/cas"}, "ck_data": {"token=; Max-Age=0; Path=/"}}
		_, _, status, err := client.serviceExchange(ctx, http.MethodPost, "https://"+vpnHost+"/wengine-vpn/cookie?"+query.Encode(), nil, http.Header{"X-Requested-With": {"XMLHttpRequest"}})
		if err != nil {
			return err
		}
		if status != 200 {
			return serviceFailure("SERVICE_AUTH_REQUIRED", "无法清理已失效的子服务会话，请恢复登录")
		}
		return nil
	}
	for _, saved := range client.jar.Snapshot() {
		target, err := url.Parse(saved.URL)
		if err != nil {
			continue
		}
		domain := strings.TrimPrefix(saved.Cookie.Domain, ".")
		if domain == "" {
			domain = target.Hostname()
		}
		if domain == campusServices[service].host && saved.Cookie.Name == "token" {
			cookie := saved.Cookie
			cookie.MaxAge = -1
			client.jar.SetCookies(target, []*http.Cookie{&cookie})
		}
	}
	return nil
}

func (client *Client) ensureService(ctx context.Context, service, mode string, force bool) error {
	if remaining := time.Until(client.serviceCooldown[service]); remaining > 0 {
		return serviceFailure("SERVICE_RATE_LIMIT", fmt.Sprintf("学校限制了请求频率，请约%d秒后重试", int(remaining.Seconds())+1))
	}
	config := campusServices[service]
	key := service + ":" + mode
	if client.serviceReady == nil {
		client.serviceReady = map[string]bool{}
	}
	if client.serviceReady[key] && !force {
		return nil
	}
	client.serviceReady[key] = false
	if force {
		if err := client.clearServiceToken(ctx, service, mode); err != nil {
			return err
		}
	}
	target, err := routeServiceURL("https://"+config.host+config.callback, mode)
	if err != nil {
		return err
	}
	for hop := 0; hop < 9; hop++ {
		target, err = serviceRedirect(target, config, mode)
		if err != nil {
			return err
		}
		payload, headers, status, err := client.serviceExchange(ctx, http.MethodGet, target, nil, http.Header{})
		if err != nil {
			return err
		}
		if delay := serviceRateLimit(payload, headers, status); delay > 0 {
			return client.recordServiceLimit(service, delay)
		}
		if status >= 500 {
			return serviceFailure("SERVICE_TEMPORARILY_UNAVAILABLE", "子服务暂时不可用，请稍后重试")
		}
		if err := serviceAccess(payload); err != nil {
			return err
		}
		if status >= 300 && status < 400 {
			location := headers.Get("Location")
			if location == "" {
				return serviceFailure("UPSTREAM_REDIRECT", "子服务登录跳转不完整")
			}
			current, _ := url.Parse(target)
			next, err := url.Parse(location)
			if err != nil {
				return serviceFailure("UNTRUSTED_REDIRECT", "子服务跳转地址无效")
			}
			target = current.ResolveReference(next).String()
			continue
		}
		normalized := target
		if mode == "webvpn" {
			normalized, err = RestoreServiceURL(target, "https://"+config.host)
			if err != nil {
				return serviceFailure("SERVICE_AUTH_REQUIRED", "请完成该子服务的登录验证")
			}
		}
		final, _ := url.Parse(normalized)
		if (status < 200 || status >= 300) && status != http.StatusUnauthorized {
			return serviceFailure("UPSTREAM_HTTP_ERROR", "子服务登录入口暂时不可用")
		}
		if status == http.StatusUnauthorized && final.Hostname() == config.host {
			return &Error{Code: "SERVICE_AUTH_REQUIRED", Message: "请恢复子服务登录", ConfirmedServiceExpiry: true}
		}
		if final.Hostname() != config.host || status == http.StatusUnauthorized {
			return serviceFailure("SERVICE_AUTH_REQUIRED", "请恢复子服务登录")
		}
		for _, path := range config.loginPaths {
			if final.Path == path {
				return serviceFailure("SERVICE_AUTH_REQUIRED", "请恢复子服务登录")
			}
		}
		if serviceNeedsAuth(service, final.Path, status, payload) {
			return serviceFailure("SERVICE_AUTH_REQUIRED", "请恢复子服务登录")
		}
		if service == "jwxk" {
			token, err := client.serviceToken(ctx, service, mode, "/xsxk/web/now")
			if err != nil {
				return err
			}
			if token == "" {
				return serviceFailure("SERVICE_AUTH_REQUIRED", "选课系统没有返回有效登录令牌")
			}
		}
		client.serviceReady[key] = true
		return nil
	}
	return serviceFailure("UPSTREAM_REDIRECT", "子服务跳转次数过多")
}

func (client *Client) recordServiceLimit(service string, delay time.Duration) error {
	if client.serviceCooldown == nil {
		client.serviceCooldown = map[string]time.Time{}
	}
	client.serviceCooldown[service] = time.Now().Add(delay)
	return serviceFailure("SERVICE_RATE_LIMIT", fmt.Sprintf("学校限制了请求频率，请约%d秒后重试", int(delay.Seconds())))
}

func (client *Client) VerifyServiceSession(ctx context.Context, service, mode string) error {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if _, exists := campusServices[service]; !exists || mode != "webvpn" {
		return serviceFailure("INVALID_REQUEST", "子服务恢复目标无效")
	}
	if err := client.ensureService(ctx, service, mode, false); err != nil {
		return err
	}
	client.setStatus(true, mode)
	return nil
}

func (client *Client) authenticateDirectService(ctx context.Context, service, password string) (*Client, error) {
	config, exists := campusServices[service]
	if !exists || password == "" || utf8.RuneCountInString(password) > 256 {
		return nil, serviceFailure("INVALID_REQUEST", "子服务恢复参数无效")
	}
	candidate, err := NewClient(client.account)
	if err != nil {
		return nil, err
	}
	candidate.http.Transport = client.http.Transport
	if err := candidate.loginDirectTarget(ctx, password, "https://"+config.host+config.callback, config.host); err != nil {
		return nil, err
	}
	if err := candidate.ensureService(ctx, service, "direct", false); err != nil {
		return nil, err
	}
	if err := candidate.VerifySaved(ctx); err != nil {
		return nil, err
	}
	return candidate, nil
}

func (client *Client) RecoverDirectService(ctx context.Context, service, password string) error {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if active, _ := client.Status(); !active {
		return serviceFailure("AUTH_REQUIRED", "请先恢复学校登录")
	}
	candidate, err := client.authenticateDirectService(ctx, service, password)
	if err != nil {
		return err
	}
	host := campusServices[service].host
	belongs := func(saved SavedCookie) bool {
		target, err := url.Parse(saved.URL)
		domain := strings.TrimPrefix(saved.Cookie.Domain, ".")
		return err == nil && target.Scheme == "https" && target.Host == host && (domain == "" || domain == host)
	}
	var incoming []SavedCookie
	for _, saved := range candidate.jar.Snapshot() {
		if belongs(saved) {
			incoming = append(incoming, saved)
		}
	}
	if len(incoming) == 0 {
		return serviceFailure("SERVICE_AUTH_REQUIRED", "学校没有返回该子服务的独立会话")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	for _, saved := range client.jar.Snapshot() {
		if belongs(saved) {
			target, _ := url.Parse(saved.URL)
			saved.Cookie.MaxAge = -1
			client.jar.SetCookies(target, []*http.Cookie{&saved.Cookie})
		}
	}
	for _, saved := range incoming {
		target, _ := url.Parse(saved.URL)
		client.jar.SetCookies(target, []*http.Cookie{&saved.Cookie})
	}
	if client.serviceReady == nil {
		client.serviceReady = map[string]bool{}
	}
	client.serviceReady[service+":direct"] = true
	return nil
}

func (client *Client) RequestService(ctx context.Context, input ServiceRequest) ([]byte, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	config, exists := campusServices[input.Service]
	if !exists {
		return nil, serviceFailure("INVALID_REQUEST", "子服务未登记")
	}
	parsed, err := servicePath(config, input.Path)
	if err != nil {
		return nil, err
	}
	mode := input.Mode
	if mode == "" || mode == "inherit" {
		mode = client.mode
	}
	if mode != "direct" && mode != "webvpn" {
		return nil, serviceFailure("INVALID_REQUEST", "子服务线路无效")
	}
	if input.Method != http.MethodGet && input.Method != http.MethodPost {
		return nil, serviceFailure("INVALID_REQUEST", "子服务请求方法无效")
	}
	if input.PreserveSessionOnAuth && (input.Service != "jwxk" || !jwxkResultFeed(parsed.Path) || input.ReadOnly || input.RefreshSession) {
		return nil, serviceFailure("INVALID_REQUEST", "仅结果来源读取允许保留当前子会话")
	}
	if input.JSON != nil && (!json.Valid(input.JSON) || input.Form != nil) {
		return nil, serviceFailure("INVALID_REQUEST", "子服务请求格式无效")
	}
	if len(input.BatchID) > 128 || strings.ContainsAny(input.BatchID, "\r\n\x00") {
		return nil, serviceFailure("INVALID_REQUEST", "选课轮次标识无效")
	}
	if active, _ := client.Status(); !active {
		return nil, serviceFailure("SERVICE_NOT_SENT", "请先恢复学校登录，业务请求尚未发送")
	}
	if err := client.ensureService(ctx, input.Service, mode, input.RefreshSession); err != nil {
		return nil, err
	}
	target, _ := routeServiceURL("https://"+config.host+input.Path, mode)
	for attempt := 0; attempt < 2; attempt++ {
		token, err := client.serviceToken(ctx, input.Service, mode, parsed.Path)
		if err != nil {
			return nil, err
		}
		if input.Service == "jwxk" && token == "" {
			if err := client.ensureService(ctx, input.Service, mode, true); err != nil {
				return nil, err
			}
			token, err = client.serviceToken(ctx, input.Service, mode, parsed.Path)
			if err != nil {
				return nil, err
			}
			if token == "" {
				return nil, serviceFailure("SERVICE_NOT_SENT", "选课系统会话未建立，业务请求尚未发送")
			}
		}
		headers := http.Header{"Accept": {"application/json, text/plain, */*"}, "X-Requested-With": {"XMLHttpRequest"}}
		if input.BatchID != "" {
			headers.Set("batchid", input.BatchID)
		}
		body := []byte(input.JSON)
		if input.JSON != nil {
			headers.Set("Content-Type", "application/json; charset=UTF-8")
		}
		if token != "" {
			headers.Set("Authorization", token)
		}
		if input.Form != nil || input.Service == "jwxk" && parsed.Path == "/xsxk/web/studentInfo" {
			form := url.Values{}
			for key, values := range input.Form {
				form[key] = append([]string{}, values...)
			}
			if input.Service == "jwxk" && parsed.Path == "/xsxk/web/studentInfo" {
				form.Set("token", token)
			}
			body = []byte(form.Encode())
			headers.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		if input.Service == "jwxk" && parsed.Path == "/xsxk/elective/clazz/list" {
			if err := client.waitCatalogRequest(ctx); err != nil {
				return nil, err
			}
		}
		payload, responseHeaders, status, err := client.serviceExchange(ctx, input.Method, target, body, headers)
		if err != nil {
			return nil, err
		}
		if err := serviceAccess(payload); err != nil {
			return nil, err
		}
		if delay := serviceRateLimit(payload, responseHeaders, status); delay > 0 {
			return nil, client.recordServiceLimit(input.Service, delay)
		}
		if serviceNeedsAuth(input.Service, parsed.Path, status, payload) {
			if !input.PreserveSessionOnAuth {
				client.serviceReady[input.Service+":"+mode] = false
			}
			if input.ReadOnly && attempt == 0 {
				if err := client.ensureService(ctx, input.Service, mode, true); err != nil {
					return nil, err
				}
				continue
			}
			return nil, &Error{Code: "SERVICE_AUTH_REQUIRED", Message: "子服务会话已失效；已发送操作不会自动重试", ConfirmedServiceExpiry: input.ReadOnly && status == http.StatusUnauthorized}
		}
		if status < 200 || status >= 300 {
			if input.Service == "cxcy" && status == http.StatusNotFound {
				return nil, serviceFailure("UPSTREAM_NOT_FOUND", "创院页面已不存在")
			}
			return nil, serviceFailure("UPSTREAM_HTTP_ERROR", "子服务暂时不可用，请稍后重试")
		}
		return payload, nil
	}
	return nil, serviceFailure("SERVICE_AUTH_REQUIRED", "请恢复子服务登录")
}

func jwxkResultFeed(path string) bool {
	switch path {
	case "/xsxk/elective/select", "/xsxk/volunteer/select", "/xsxk/volunteer/xgxk/select", "/xsxk/elective/neu/deselect":
		return true
	}
	return false
}

func (client *Client) waitCatalogRequest(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if delay := time.Until(client.nextCatalogRequest); delay > 0 {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
		}
	}
	client.nextCatalogRequest = time.Now().Add(550*time.Millisecond + time.Duration(rand.IntN(151))*time.Millisecond)
	return nil
}
