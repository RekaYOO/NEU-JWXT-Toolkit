package campus

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const evaluationOrigin = "http://zljk.neu.edu.cn"

func evaluationAuthError() error {
	return &Error{Code: "EVALUATION_AUTH_REQUIRED", Message: "评教服务登录已失效，请恢复学校登录后重试；已发送的评价不会自动重试"}
}

func (client *Client) evaluationExchange(ctx context.Context, method, target string, body []byte, token string) ([]byte, http.Header, int, error) {
	if client.mode == "webvpn" {
		var err error
		target, err = WebVPNURL(target)
		if err != nil {
			return nil, nil, 0, err
		}
	}
	request, err := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(body))
	if err != nil {
		return nil, nil, 0, &Error{Code: "INVALID_REQUEST", Message: "无法创建评教请求"}
	}
	request.Header.Set("User-Agent", "Mozilla/5.0 NEU-JWXT-Toolkit")
	request.Header.Set("Accept", "application/json, text/plain, */*")
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
		request.Header.Set("X-Tenant-ID", "default")
		request.Header.Set("Content-Type", "application/json; charset=UTF-8")
		request.Header.Set("Origin", evaluationOrigin)
		request.Header.Set("Referer", evaluationOrigin+"/evaluate/studentEvaluate/student-jdp/index")
	}
	transport := *client.http
	transport.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := transport.Do(request)
	if err != nil {
		return nil, nil, 0, &Error{Code: "UPSTREAM_UNAVAILABLE", Message: "评教请求失败或超时，已发送操作不会自动重试"}
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, 16*1024*1024+1))
	if err != nil || len(payload) > 16*1024*1024 {
		return nil, nil, response.StatusCode, &Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "评教响应过大或未完整接收"}
	}
	return payload, response.Header, response.StatusCode, nil
}

func (client *Client) ensureEvaluationToken(ctx context.Context) error {
	if client.evaluationToken != "" && time.Until(client.evaluationExpiry) > time.Minute {
		return nil
	}
	client.evaluationToken = ""
	_, headers, status, err := client.evaluationExchange(ctx, http.MethodGet, casLogin+"?"+url.Values{"service": {evaluationOrigin + "/caslogin"}}.Encode(), nil, "")
	if err != nil {
		return err
	}
	if status != 302 && status != 303 {
		return evaluationAuthError()
	}
	location, err := url.Parse(headers.Get("Location"))
	if err != nil || location.User != nil || location.Fragment != "" {
		return evaluationAuthError()
	}
	if client.mode == "webvpn" {
		proxy, _ := WebVPNURL(evaluationOrigin + "/caslogin")
		expected, _ := url.Parse(proxy)
		if location.Scheme != expected.Scheme || location.Host != expected.Host || location.Path != expected.Path {
			return evaluationAuthError()
		}
	} else if location.Scheme != "http" || location.Host != "zljk.neu.edu.cn" || location.Path != "/caslogin" {
		return evaluationAuthError()
	}
	ticket := location.Query().Get("ticket")
	if ticket == "" || len(ticket) > 4096 || strings.ContainsAny(ticket, "\r\n\x00") {
		return evaluationAuthError()
	}
	payload, _, status, err := client.evaluationExchange(ctx, http.MethodGet, evaluationOrigin+"/api/cas_ticket_verify?"+url.Values{"ticket": {ticket}}.Encode(), nil, "")
	if err != nil {
		return err
	}
	var response struct {
		Token string `json:"accessToken"`
	}
	if status != 200 || json.Unmarshal(payload, &response) != nil || response.Token == "" || len(response.Token) > 16384 || strings.ContainsAny(response.Token, "\r\n\x00") {
		return evaluationAuthError()
	}
	expires := time.Now().Add(time.Hour)
	parts := strings.Split(response.Token, ".")
	if len(parts) == 3 {
		claims, decodeErr := base64.RawURLEncoding.DecodeString(parts[1])
		var metadata struct {
			Expiry int64 `json:"exp"`
		}
		if decodeErr == nil && json.Unmarshal(claims, &metadata) == nil && metadata.Expiry > 0 {
			expires = time.Unix(metadata.Expiry, 0)
		}
	}
	if !expires.After(time.Now()) {
		return evaluationAuthError()
	}
	client.evaluationToken, client.evaluationExpiry = response.Token, expires
	return nil
}

func (client *Client) EvaluationRequest(ctx context.Context, path string, body json.RawMessage) ([]byte, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if active, _ := client.Status(); !active {
		return nil, evaluationAuthError()
	}
	reference, err := url.Parse(path)
	if err != nil || reference.IsAbs() || reference.Host != "" || reference.Fragment != "" || !strings.HasPrefix(reference.Path, "/api/") || strings.Contains(reference.Path, "..") || strings.ContainsAny(reference.Path, "\\%\r\n\x00") {
		return nil, &Error{Code: "UNTRUSTED_URL", Message: "评教接口地址无效"}
	}
	if body != nil && !json.Valid(body) {
		return nil, &Error{Code: "INVALID_REQUEST", Message: "评教请求格式无效"}
	}
	if err := client.ensureEvaluationToken(ctx); err != nil {
		return nil, &Error{Code: "EVALUATION_NOT_SENT", Message: "评教服务连接尚未建立，未发送评价，请恢复连接后重试"}
	}
	payload, _, status, err := client.evaluationExchange(ctx, http.MethodPost, evaluationOrigin+path, body, client.evaluationToken)
	if err != nil {
		return nil, err
	}
	if status == 401 || status == 403 || (status >= 300 && status < 400) {
		client.evaluationToken = ""
		return nil, evaluationAuthError()
	}
	if status < 200 || status >= 300 {
		return nil, &Error{Code: "UPSTREAM_HTTP_ERROR", Message: "评教服务返回异常，请核对操作结果"}
	}
	return payload, nil
}
