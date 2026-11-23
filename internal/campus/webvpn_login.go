package campus

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const vpnEntry = "https://webvpn.neu.edu.cn/login?cas_login=true"

type vpnLoginFlow struct {
	id, kind, uuid, page, action, captcha string
	fields                                url.Values
	expires                               time.Time
	verified                              bool
	lastPoll                              time.Time
}

func NewLoginClient(hint string) (*Client, error) {
	if strings.TrimSpace(hint) != "" {
		return NewClient(hint)
	}
	client, err := NewClient("pending")
	if err == nil {
		client.account = ""
	}
	return client, err
}

func (client *Client) SetVPNTarget(target string) error {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if target != "primary" && target != "jwxk" && target != "cxcy" {
		return vpnFailure("INVALID_REQUEST", "认证目标无效")
	}
	if target != "primary" && client.account == "" {
		return vpnFailure("WEBVPN_ACCOUNT_UNVERIFIED", "子服务恢复必须绑定当前账号")
	}
	if client.authFlow != nil {
		return vpnFailure("WEBVPN_FLOW_REPLACED", "不能修改进行中的认证目标")
	}
	client.vpnTarget = target
	return nil
}

func vpnCompletedPage(page *url.URL) bool {
	if page == nil {
		return false
	}
	if page.Hostname() == "jwxt.neu.edu.cn" {
		return true
	}
	if page.Hostname() != vpnHost {
		return false
	}
	if page.Path == "/" || page.Path == "/portal" {
		return true
	}
	_, err := RestoreServiceURL(page.String(), "https://jwxt.neu.edu.cn")
	return err == nil
}

func loginID() (string, error) {
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return "", err
	}
	identifier[6] = (identifier[6] & 15) | 64
	identifier[8] = (identifier[8] & 63) | 128
	encoded := hex.EncodeToString(identifier)
	return encoded[:8] + "-" + encoded[8:12] + "-" + encoded[12:16] + "-" + encoded[16:20] + "-" + encoded[20:], nil
}

func vpnAuthURL(target *url.URL) bool {
	if target == nil || target.Scheme != "https" || !trustedURL(target) || strings.Contains(target.Path, "\\") {
		return false
	}
	if target.Hostname() != vpnHost {
		return target.Hostname() == "pass.neu.edu.cn" || target.Hostname() == "jwxt.neu.edu.cn"
	}
	if strings.HasPrefix(target.Path, "/https/") || strings.HasPrefix(target.Path, "/https-443/") || strings.HasPrefix(target.Path, "/http/") {
		for _, origin := range []string{"https://pass.neu.edu.cn", "https://jwxt.neu.edu.cn"} {
			if _, err := RestoreServiceURL(target.String(), origin); err == nil {
				return true
			}
		}
		return false
	}
	return target.Path == "/" || target.Path == "/login" || target.Path == "/login/cas" || target.Path == "/wengine-vpn/cookie" || target.Path == "/portal"
}

func vpnFailure(code, message string) error { return &Error{Code: code, Message: message} }

func (client *Client) vpnSend(ctx context.Context, method, raw string, form url.Values, referer string) ([]byte, *url.URL, error) {
	target, err := url.Parse(raw)
	if err != nil || !vpnAuthURL(target) {
		return nil, nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "认证地址不受信任，请重新登录")
	}
	var body io.Reader
	if form != nil {
		body = strings.NewReader(form.Encode())
	}
	request, err := http.NewRequestWithContext(ctx, method, raw, body)
	if err != nil {
		return nil, nil, vpnFailure("WEBVPN_UNKNOWN_ERROR", "无法创建认证请求")
	}
	request.Header.Set("User-Agent", "Mozilla/5.0 NEU-JWXT-Toolkit")
	request.Header.Set("Referer", referer)
	request.Header.Set("X-Requested-With", "XMLHttpRequest")
	if form != nil {
		request.Header.Set("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
	}
	transport := *client.http
	transport.CheckRedirect = func(next *http.Request, previous []*http.Request) error {
		if next.Method == "GET" && next.URL.Scheme == "http" && next.URL.Hostname() == "pass.neu.edu.cn" && (next.URL.Port() == "" || next.URL.Port() == "80") && next.URL.Path == "/tpass/login" {
			next.URL.Scheme, next.URL.Host = "https", "pass.neu.edu.cn"
		}
		if len(previous) > 0 && previous[len(previous)-1].URL.Hostname() == vpnHost && next.URL.Hostname() != vpnHost && vpnAuthURL(next.URL) {
			converted, err := WebVPNURL(next.URL.String())
			if err != nil {
				return vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "认证跳转地址无效")
			}
			next.URL, _ = url.Parse(converted)
			next.Host = next.URL.Host
		}
		if len(previous) >= 10 || !vpnAuthURL(next.URL) || (next.Method != "GET" && next.Method != "HEAD") {
			return vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "认证跳转被拒绝；已发送表单不会重放")
		}
		return nil
	}
	response, err := transport.Do(request)
	if err != nil {
		var failure *Error
		if errors.As(err, &failure) {
			return nil, nil, failure
		}
		return nil, nil, vpnFailure("WEBVPN_UPSTREAM_TIMEOUT", "学校认证请求失败或超时；已发送表单不会自动重试")
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, 2*1024*1024+1))
	if err != nil || len(payload) > 2*1024*1024 {
		return nil, response.Request.URL, vpnFailure("WEBVPN_UNKNOWN_ERROR", "认证响应未完整接收")
	}
	if response.StatusCode == 403 && response.Request.URL.Hostname() == vpnHost {
		for _, marker := range []string{"WebVPN仅用于我校师生在校外登录", "校园网用户无需使用WebVPN"} {
			if strings.Contains(string(payload), marker) {
				return nil, response.Request.URL, vpnFailure("WEBVPN_CAMPUS_NETWORK", "校园网无法使用WebVPN，请切换校内直连")
			}
		}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, response.Request.URL, vpnFailure("WEBVPN_UNKNOWN_ERROR", "学校认证接口暂不可用")
	}
	return payload, response.Request.URL, nil
}

func authForm(payload []byte, page *url.URL, identifier string) (url.Values, string, bool) {
	document, err := html.Parse(strings.NewReader(string(payload)))
	if err != nil {
		return nil, "", false
	}
	var selected *html.Node
	attr := func(node *html.Node, key string) string {
		for _, attribute := range node.Attr {
			if attribute.Key == key {
				return attribute.Val
			}
		}
		return ""
	}
	var find func(*html.Node)
	find = func(node *html.Node) {
		if selected != nil {
			return
		}
		if node.Type == html.ElementNode && node.Data == "form" && (attr(node, "id") == identifier || attr(node, "name") == identifier) {
			selected = node
			return
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			find(child)
		}
	}
	find(document)
	if selected == nil {
		return nil, "", false
	}
	action, err := page.Parse(attr(selected, "action"))
	if err != nil || !vpnAuthURL(action) {
		return nil, "", false
	}
	fields := url.Values{}
	var collect func(*html.Node)
	collect = func(node *html.Node) {
		if node.Type == html.ElementNode && node.Data == "input" && attr(node, "name") != "" {
			fields.Set(attr(node, "name"), attr(node, "value"))
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			collect(child)
		}
	}
	collect(selected)
	return fields, action.String(), true
}

func (client *Client) StartVPNQR(ctx context.Context) (map[string]any, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.authFlow = nil
	client.vpnSource, client.vpnServiceState, client.vpnGatewayVerified = "qr", "", false
	client.setStatus(false, "webvpn")
	login := casLogin + "?" + url.Values{"service": {vpnEntry}}.Encode()
	_, page, err := client.vpnSend(ctx, "GET", login, nil, "")
	if err != nil {
		return nil, err
	}
	if page.Hostname() != "pass.neu.edu.cn" || page.Path != "/tpass/login" {
		return nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "统一认证未进入扫码页面")
	}
	identifier, err := loginID()
	if err != nil {
		return nil, err
	}
	uuid, err := loginID()
	if err != nil {
		return nil, err
	}
	client.authFlow = &vpnLoginFlow{id: identifier, kind: "qr", uuid: uuid, page: login, expires: time.Now().Add(180 * time.Second)}
	return map[string]any{"status": "pending", "flow_id": identifier, "qr_content": "https://pass.neu.edu.cn/tpass/qyQrLogin?uuid=" + uuid + "&service=" + vpnEntry, "expires_in": 180, "poll_interval": 3}, nil
}

func (client *Client) vpnFlow(identifier, kind string) (*vpnLoginFlow, error) {
	flow := client.authFlow
	if flow == nil || flow.id != identifier || flow.kind != kind {
		return nil, vpnFailure("WEBVPN_FLOW_REPLACED", "登录流程不存在或已被替换")
	}
	if !time.Now().Before(flow.expires) {
		client.authFlow = nil
		return nil, vpnFailure("WEBVPN_FLOW_EXPIRED", "登录流程已过期，请重新开始")
	}
	return flow, nil
}

func (client *Client) PollVPNQR(ctx context.Context, identifier string) (map[string]any, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	flow, err := client.vpnFlow(identifier, "qr")
	if err != nil {
		return nil, err
	}
	if time.Since(flow.lastPoll) < 3*time.Second {
		return map[string]any{"status": "pending"}, nil
	}
	flow.lastPoll = time.Now()
	endpoint := "https://pass.neu.edu.cn/tpass/checkQRCodeScan?" + url.Values{"uuid": {flow.uuid}, "random": {fmt.Sprint(time.Now().UnixNano())}}.Encode()
	payload, _, err := client.vpnSend(ctx, "GET", endpoint, nil, flow.page)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(string(payload)) == "" {
		return map[string]any{"status": "pending"}, nil
	}
	var result map[string]any
	if json.Unmarshal(payload, &result) != nil || result == nil {
		return nil, vpnFailure("WEBVPN_UPSTREAM_NON_JSON", "扫码状态响应无法识别")
	}
	redirect, _ := result["redirect_url"].(string)
	if redirect == "" {
		return map[string]any{"status": "pending"}, nil
	}
	base, _ := url.Parse(flow.page)
	callback, err := base.Parse(redirect)
	if err != nil || !vpnAuthURL(callback) || (callback.Hostname() != "pass.neu.edu.cn" && callback.Hostname() != vpnHost) {
		return nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "扫码回调地址不受信任")
	}
	payload, page, err := client.vpnSend(ctx, "GET", callback.String(), nil, flow.page)
	if err != nil {
		return nil, err
	}
	if fields, action, ok := authForm(payload, page, "second_auth_form"); ok {
		return client.beginVPNSMS(ctx, flow.id, page, action, fields)
	}
	if err := client.completeVPN(ctx); err != nil {
		return nil, err
	}
	client.authFlow = nil
	return client.vpnAuthenticated(), nil
}

func (client *Client) StartVPNPassword(ctx context.Context, password string) (map[string]any, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if client.account == "" || password == "" || len(password) > 1024 {
		return nil, vpnFailure("INVALID_PASSWORD", "请输入有效账号和密码")
	}
	client.authFlow = nil
	client.vpnSource, client.vpnServiceState, client.vpnGatewayVerified = "password", "", false
	client.setStatus(false, "webvpn")
	payload, page, err := client.vpnSend(ctx, "GET", vpnEntry, nil, "")
	if err != nil {
		return nil, err
	}
	if page.Hostname() != vpnHost || !strings.HasSuffix(page.Path, "/tpass/login") {
		return nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "未能打开WebVPN统一认证页面")
	}
	hidden, key, _ := loginFields(payload)
	if key == "" {
		key = defaultPublicKey
	}
	form, err := credentialForm(client.account, password, key, hidden)
	if err != nil {
		return nil, vpnFailure("BAD_KEY", "学校登录加密参数不可用")
	}
	action := page.String()
	if _, resolved, ok := authForm(payload, page, "loginForm"); ok {
		action = resolved
	}
	actionURL, _ := url.Parse(action)
	if actionURL.Hostname() != vpnHost || !strings.HasSuffix(actionURL.Path, "/tpass/login") {
		return nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "登录表单地址不受信任")
	}
	payload, page, err = client.vpnSend(ctx, "POST", action, form, page.String())
	if err != nil {
		return nil, err
	}
	if fields, action, ok := authForm(payload, page, "second_auth_form"); ok {
		identifier, err := loginID()
		if err != nil {
			return nil, err
		}
		return client.beginVPNSMS(ctx, identifier, page, action, fields)
	}
	_, _, message := loginFields(payload)
	if passwordRejected(message) {
		return nil, vpnFailure("WRONG_PASSWORD", "账号或密码错误")
	}
	if strings.HasSuffix(page.Path, "/tpass/login") {
		return nil, vpnFailure("LOGIN_REJECTED", "学校认证尚未完成，请重新登录")
	}
	client.vpnGatewayVerified = vpnCompletedPage(page)
	if err := client.completeVPN(ctx); err != nil {
		return nil, err
	}
	return client.vpnAuthenticated(), nil
}

func (client *Client) beginVPNSMS(ctx context.Context, identifier string, page *url.URL, action string, fields url.Values) (map[string]any, error) {
	resolved, resolveError := RestoreServiceURL(action, "https://pass.neu.edu.cn")
	if page.Hostname() != vpnHost || !strings.Contains(page.Path, "/tpass/") || resolveError != nil || !strings.HasPrefix(resolved, "https://pass.neu.edu.cn/tpass/") {
		return nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "二次认证地址不受信任")
	}
	client.authFlow = &vpnLoginFlow{id: identifier, kind: "sms", page: page.String(), action: action, fields: fields, expires: time.Now().Add(6 * time.Minute)}
	result, err := client.vpnCaptcha(ctx, client.authFlow)
	if result == nil {
		result = map[string]any{}
	}
	result["status"], result["flow_id"], result["expires_in"] = "sms_required", identifier, 300
	return result, err
}

func (client *Client) vpnCaptcha(ctx context.Context, flow *vpnLoginFlow) (map[string]any, error) {
	flow.captcha = ""
	page, _ := url.Parse(flow.page)
	endpoint := page.ResolveReference(&url.URL{Path: "code", RawQuery: "vpn-1&" + fmt.Sprint(time.Now().UnixNano())})
	payload, final, err := client.vpnSend(ctx, "GET", endpoint.String(), nil, flow.page)
	if err != nil {
		return nil, err
	}
	if final.Hostname() != vpnHost || !strings.HasSuffix(final.Path, "/tpass/code") {
		return nil, vpnFailure("WEBVPN_UPSTREAM_REDIRECT", "验证码请求被重定向，请重新登录")
	}
	media := ""
	switch {
	case len(payload) >= 6 && (string(payload[:6]) == "GIF87a" || string(payload[:6]) == "GIF89a"):
		media = "image/gif"
	case len(payload) >= 8 && string(payload[:8]) == "\x89PNG\r\n\x1a\n":
		media = "image/png"
	case len(payload) >= 3 && string(payload[:3]) == "\xff\xd8\xff":
		media = "image/jpeg"
	case len(payload) >= 12 && string(payload[:4]) == "RIFF" && string(payload[8:12]) == "WEBP":
		media = "image/webp"
	}
	if media == "" {
		return nil, vpnFailure("WEBVPN_CAPTCHA_FETCH_FAILED", "学校未返回有效验证码图片，请刷新重试")
	}
	flow.captcha = ""
	return map[string]any{"captcha_image": "data:" + media + ";base64," + base64.StdEncoding.EncodeToString(payload)}, nil
}

func (client *Client) RefreshVPNCaptcha(ctx context.Context, identifier string) (map[string]any, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	flow, err := client.vpnFlow(identifier, "sms")
	if err != nil {
		return nil, err
	}
	if flow.verified {
		return vpnSessionPending(flow), nil
	}
	result, err := client.vpnCaptcha(ctx, flow)
	if err != nil {
		return nil, err
	}
	flow.expires = time.Now().Add(6 * time.Minute)
	result["status"], result["expires_in"] = "captcha_refreshed", 300
	return result, nil
}

func (client *Client) SendVPNSMS(ctx context.Context, identifier, captcha string) (map[string]any, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	flow, err := client.vpnFlow(identifier, "sms")
	if err != nil {
		return nil, err
	}
	if flow.verified {
		return vpnSessionPending(flow), nil
	}
	captcha = strings.TrimSpace(captcha)
	if captcha == "" || len(captcha) > 32 {
		return nil, vpnFailure("WEBVPN_CAPTCHA_INVALID", "请输入图形验证码")
	}
	flow.captcha = captcha
	page, _ := url.Parse(flow.page)
	endpoint := page.ResolveReference(&url.URL{Path: "secondAuthCode", RawQuery: "vpn-12-o2-pass.neu.edu.cn"})
	payload, _, err := client.vpnSend(ctx, "POST", endpoint.String(), url.Values{"code": {captcha}, "method": {"mobile"}}, flow.page)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if json.Unmarshal(payload, &result) != nil || result == nil {
		return nil, vpnFailure("WEBVPN_UPSTREAM_NON_JSON", "短信接口返回了无法识别的响应")
	}
	code, message := vpnResponseCode(result)
	if graphicCaptchaRejected(code, message) {
		return client.vpnCaptchaRejected(ctx, flow)
	}
	if code == "send" || code == "success" || code == "ok" || code == "0" || result["success"] == true {
		flow.expires = time.Now().Add(6 * time.Minute)
		return map[string]any{"status": "sent", "expires_in": 300}, nil
	}
	if code == "max" || strings.Contains(message, "频繁") {
		return nil, vpnFailure("WEBVPN_SMS_RATE_LIMITED", "发送过于频繁，请稍后重试")
	}
	if code == "unknow" {
		return nil, vpnFailure("WEBVPN_SMS_PHONE_UNBOUND", "统一认证未绑定手机号")
	}
	return nil, vpnFailure("WEBVPN_SMS_INVALID", "短信验证码发送失败，请核对后重试")
}

func vpnResponseCode(result map[string]any) (string, string) {
	code := ""
	for _, key := range []string{"info", "code", "status"} {
		if value, ok := result[key]; ok && value != nil {
			code = strings.ToLower(fmt.Sprint(value))
			break
		}
	}
	message, _ := result["message"].(string)
	if message == "" {
		message, _ = result["msg"].(string)
	}
	return code, message
}

func graphicCaptchaRejected(code, message string) bool {
	if code == "codeerr" || code == "captcha_error" || code == "captcha_invalid" {
		return true
	}
	if strings.Contains(message, "短信") {
		return false
	}
	for _, kind := range []string{"图形验证码", "图片验证码", "验证码错误", "验证码不正确", "校验码"} {
		for _, failure := range []string{"错误", "不正确", "失败", "失效", "过期", "不匹配", "为空"} {
			if strings.Contains(message, kind) && strings.Contains(message, failure) {
				return true
			}
		}
	}
	return false
}

func (client *Client) vpnCaptchaRejected(ctx context.Context, flow *vpnLoginFlow) (map[string]any, error) {
	result, _ := client.vpnCaptcha(ctx, flow)
	if result == nil {
		result = map[string]any{}
	}
	result["status"], result["flow_id"], result["expires_in"] = "sms_required", flow.id, max(0, int(time.Until(flow.expires).Seconds())-60)
	return result, vpnFailure("WEBVPN_CAPTCHA_INVALID", "图形验证码错误，请重新填写")
}

func (client *Client) VerifyVPNSMS(ctx context.Context, identifier, code string) (map[string]any, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	flow, err := client.vpnFlow(identifier, "sms")
	if err != nil {
		return nil, err
	}
	if !flow.verified {
		code = strings.TrimSpace(code)
		if code == "" || len(code) > 32 {
			return nil, vpnFailure("WEBVPN_SMS_INVALID", "请输入有效短信验证码")
		}
		form := url.Values{}
		for key, values := range flow.fields {
			form[key] = append([]string{}, values...)
		}
		form.Set("imgCode", flow.captcha)
		form.Set("scendAuthCode", code)
		if form.Get("method") == "" {
			form.Set("method", "mobile")
		}
		if form.Get("_eventId") == "" {
			form.Set("_eventId", "submit")
		}
		payload, page, err := client.vpnSend(ctx, "POST", flow.action, form, flow.page)
		if err != nil {
			return nil, err
		}
		var result map[string]any
		json.Unmarshal(payload, &result)
		status, message := vpnResponseCode(result)
		if graphicCaptchaRejected(status, message) {
			return client.vpnCaptchaRejected(ctx, flow)
		}
		_, _, challenge := authForm(payload, page, "second_auth_form")
		if challenge || strings.HasSuffix(page.Path, "/tpass/login") || status == "timeout" || status == "codeerror" || status == "sms_error" || status == "invalid" || result["success"] == false {
			return nil, vpnFailure("WEBVPN_SMS_INVALID", "短信验证码错误或过期，请核对后重试")
		}
		flow.verified = true
		client.vpnGatewayVerified = vpnCompletedPage(page)
		flow.expires = time.Now().Add(5 * time.Minute)
		flow.fields = nil
		flow.captcha = ""
	}
	if err := client.completeVPN(ctx); err != nil {
		if failure, ok := err.(*Error); ok && (failure.Code == "WEBVPN_ACCOUNT_MISMATCH" || failure.Code == "WEBVPN_ACCOUNT_UNVERIFIED" || failure.Code == "WEBVPN_CAMPUS_NETWORK") {
			client.authFlow = nil
			return nil, err
		}
		return vpnSessionPending(flow), nil
	}
	client.authFlow = nil
	return client.vpnAuthenticated(), nil
}

func vpnSessionPending(flow *vpnLoginFlow) map[string]any {
	return map[string]any{"status": "session_pending", "sms_verified": true, "flow_id": flow.id, "expires_in": max(0, int(time.Until(flow.expires).Seconds())), "error_code": "WEBVPN_SESSION_ESTABLISH_FAILED", "message": "短信已验证，请继续建立会话，无需再次提交短信验证码"}
}
func (client *Client) vpnAuthenticated() map[string]any {
	target := client.vpnTarget
	if target == "" {
		target = "primary"
	}
	state := client.vpnServiceState
	if state == "" {
		state = "authenticated"
	}
	return map[string]any{"status": "authenticated", "username": client.account, "network_mode": "webvpn", "target_service": target, "service_auth_state": state}
}

func (client *Client) completeVPNService(ctx context.Context) error {
	client.vpnServiceState = "authenticated"
	if err := client.ensureService(ctx, client.vpnTarget, "webvpn", false); err != nil {
		client.vpnServiceState = "service_unavailable"
		var failure *Error
		if errors.As(err, &failure) {
			if failure.Code == "WEBVPN_CAMPUS_NETWORK" {
				return err
			}
			if failure.Code == "JWXK_NOT_IN_SELECTION_ROUND" {
				client.vpnServiceState = "not_in_selection_round"
			}
			if failure.Code == "SERVICE_AUTH_REQUIRED" {
				client.vpnServiceState = "login_required"
			}
		}
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	client.setStatus(true, "webvpn")
	return nil
}

func (client *Client) completeVPN(ctx context.Context) error {
	cas, _ := url.Parse(casLogin)
	for _, cookie := range client.jar.Cookies(cas) {
		if cookie.Name != "CASTGC" {
			continue
		}
		endpoint := "https://" + vpnHost + "/wengine-vpn/cookie?" + url.Values{"method": {"set"}, "host": {"pass.neu.edu.cn"}, "scheme": {"https"}, "path": {"/tpass/login"}, "ck_data": {"CASTGC=" + cookie.Value}}.Encode()
		if _, _, err := client.vpnSend(ctx, "POST", endpoint, url.Values{}, vpnEntry); err != nil {
			return err
		}
		break
	}
	if _, service := campusServices[client.vpnTarget]; service && client.vpnSource == "password" && client.vpnGatewayVerified && client.account != "" {
		return client.completeVPNService(ctx)
	}
	var lastFailure error
	hadResponse := false
	for attempt := 0; attempt < 2; attempt++ {
		if attempt == 1 {
			endpoint, _ := WebVPNURL(primaryService)
			client.vpnSend(ctx, "GET", endpoint, nil, vpnEntry)
		}
		endpoint, _ := WebVPNURL("https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do")
		payload, _, err := client.vpnSend(ctx, "POST", endpoint, url.Values{}, vpnEntry)
		if err != nil {
			lastFailure = err
			if failure, ok := err.(*Error); ok && failure.Code == "WEBVPN_CAMPUS_NETWORK" {
				return err
			}
			continue
		}
		hadResponse = true
		var result struct {
			Code json.RawMessage `json:"code"`
			Data struct {
				Account string `json:"userId"`
			} `json:"datas"`
		}
		if json.Unmarshal(payload, &result) != nil || (string(result.Code) != "0" && string(result.Code) != `"0"`) {
			continue
		}
		account, err := NormalizeAccount(result.Data.Account)
		if err != nil {
			continue
		}
		if client.account != "" && client.account != account {
			return vpnFailure("WEBVPN_ACCOUNT_MISMATCH", "扫码或认证账号与当前账号不一致，请重新登录")
		}
		client.account = account
		if _, service := campusServices[client.vpnTarget]; service {
			return client.completeVPNService(ctx)
		}
		client.setStatus(true, "webvpn")
		return nil
	}
	if _, service := campusServices[client.vpnTarget]; service && client.vpnSource == "qr" {
		return vpnFailure("WEBVPN_ACCOUNT_UNVERIFIED", "暂时无法核验扫码账号归属，请改用同账号密码恢复；当前主会话未改变")
	}
	if !hadResponse && lastFailure != nil {
		return lastFailure
	}
	return vpnFailure("WEBVPN_SESSION_ESTABLISH_FAILED", "学校身份尚未核验，请继续建立会话")
}

func (client *Client) VerifyVPN(ctx context.Context) error {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.vpnTarget, client.vpnSource = "primary", ""
	client.vpnGatewayVerified = false
	client.setStatus(false, "webvpn")
	return client.completeVPN(ctx)
}
