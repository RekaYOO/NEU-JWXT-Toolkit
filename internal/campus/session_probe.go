package campus

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
)

func (client *Client) VerifySaved(ctx context.Context) error {
	_, mode := client.Status()
	if mode == "webvpn" {
		return client.VerifyVPN(ctx)
	}
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.setStatus(false, "direct")
	payload, final, err := client.send(ctx, "POST", "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do", url.Values{})
	if err != nil {
		return err
	}
	if final.Hostname() != "jwxt.neu.edu.cn" {
		return &Error{Code: "AUTH_REQUIRED", Message: "学校会话需要恢复"}
	}
	if err := client.checkIdentityResponse(payload); err != nil {
		return err
	}
	client.setStatus(true, "direct")
	return nil
}

func (client *Client) ProbeSession(ctx context.Context) error {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if active, _ := client.Status(); !active {
		return &Error{Code: "AUTH_REQUIRED", Message: "学校会话需要恢复"}
	}
	err := client.probeCurrentIdentity(ctx)
	var failure *Error
	if errors.As(err, &failure) && (failure.Code == "AUTH_REQUIRED" || failure.Code == "AUTH_ACCOUNT_MISMATCH" || failure.Code == "WEBVPN_CAMPUS_NETWORK") {
		client.setStatus(false, client.mode)
	}
	return err
}

func (client *Client) probeCurrentIdentity(ctx context.Context) error {
	endpoint := "https://jwxt.neu.edu.cn/jwapp/sys/homeapp/api/home/currentUser.do"
	var payload []byte
	var final *url.URL
	var err error
	if client.mode == "webvpn" {
		endpoint, _ = WebVPNURL(endpoint)
		payload, final, err = client.vpnSend(ctx, "POST", endpoint, url.Values{}, vpnEntry)
	} else {
		payload, final, err = client.send(ctx, "POST", endpoint, url.Values{})
	}
	if err != nil {
		return err
	}
	if final.Hostname() == "pass.neu.edu.cn" || final.Hostname() == vpnHost && strings.HasPrefix(final.Path, "/login") {
		return &Error{Code: "AUTH_REQUIRED", Message: "学校会话需要恢复"}
	}
	if final.Hostname() == vpnHost {
		if _, err := RestoreServiceURL(final.String(), "https://jwxt.neu.edu.cn"); err != nil || client.mode != "webvpn" {
			return &Error{Code: "AUTH_REQUIRED", Message: "学校会话需要恢复"}
		}
	}
	return client.checkIdentityResponse(payload)
}

func (client *Client) checkIdentityResponse(payload []byte) error {
	var result struct {
		Code    json.RawMessage `json:"code"`
		Message string          `json:"message"`
		Data    struct {
			Account string `json:"userId"`
		} `json:"datas"`
	}
	if json.Unmarshal(payload, &result) != nil {
		return &Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校身份核验响应暂不可用"}
	}
	code := strings.Trim(string(result.Code), `"`)
	if code != "0" {
		if code == "401" || code == "403" || code == "-1" {
			return &Error{Code: "AUTH_REQUIRED", Message: "学校会话需要恢复"}
		}
		for _, marker := range []string{"未登录", "请先登录", "请重新登录", "登录已过期", "登录已失效"} {
			if strings.Contains(result.Message, marker) {
				return &Error{Code: "AUTH_REQUIRED", Message: "学校会话需要恢复"}
			}
		}
		return &Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校身份核验暂未完成"}
	}
	account, err := NormalizeAccount(result.Data.Account)
	if err != nil {
		return &Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校未返回有效身份，请稍后重试"}
	}
	if account != client.account {
		return &Error{Code: "AUTH_ACCOUNT_MISMATCH", Message: "学校会话身份不匹配，请重新登录"}
	}
	return nil
}
