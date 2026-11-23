package campus

import (
	"context"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const MaxAcademicDocumentBytes = 25 * 1024 * 1024

type AcademicDocumentFile struct {
	Body        []byte
	ContentType string
}

func (client *Client) GenerateAcademicDocument(ctx context.Context, identifier string) (AcademicDocumentFile, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if !client.loggedIn {
		return AcademicDocumentFile{}, &Error{Code: "AUTH_REQUIRED", Message: "请先登录"}
	}
	if identifier == "" || len(identifier) > 128 || strings.ContainsAny(identifier, "\r\n\x00") {
		return AcademicDocumentFile{}, &Error{Code: "INVALID_REQUEST", Message: "证明编号无效"}
	}
	target := "https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/zmdy/printZm.do?" + url.Values{"wid": {identifier}}.Encode()
	referer := "https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/*default/index.do"
	if client.mode == "webvpn" {
		var err error
		target, err = WebVPNURL(target)
		if err != nil {
			return AcademicDocumentFile{}, err
		}
		referer, err = WebVPNURL(referer)
		if err != nil {
			return AcademicDocumentFile{}, err
		}
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return AcademicDocumentFile{}, err
	}
	request.Header.Set("Accept", "application/pdf,text/html,*/*")
	request.Header.Set("Referer", referer)
	request.Header.Set("User-Agent", "Mozilla/5.0 NEU-JWXT-Toolkit")
	transport := client.http.Transport
	if transport == nil {
		transport = http.DefaultTransport
	}
	if base, ok := transport.(*http.Transport); ok {
		isolated := base.Clone()
		isolated.DisableKeepAlives = true
		defer isolated.CloseIdleConnections()
		transport = isolated
	}
	once := *client.http
	once.Transport = transport
	once.CheckRedirect = func(next *http.Request, previous []*http.Request) error {
		if len(previous) >= 10 || !trustedURL(next.URL) || strings.HasSuffix(next.URL.Path, "/printZm.do") {
			return &Error{Code: "UPSTREAM_REDIRECT", Message: "证明生成跳转异常，请先核对学校打印记录"}
		}
		if next.URL.Hostname() != "jwxt.neu.edu.cn" && next.URL.Hostname() != vpnHost {
			return &Error{Code: "AUTH_EXPIRED", Message: "证明服务登录已失效，请重新登录；不会重发生成请求"}
		}
		return nil
	}
	response, err := once.Do(request)
	if err != nil {
		return AcademicDocumentFile{}, &Error{Code: "DOCUMENT_RESULT_UNKNOWN", Message: "证明生成结果尚未确认，请先核对学校打印记录；不会自动重试"}
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return AcademicDocumentFile{}, &Error{Code: "DOCUMENT_RESULT_UNKNOWN", Message: "学校未返回有效证明，请核对打印记录后再操作"}
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, MaxAcademicDocumentBytes+1))
	if err != nil || len(body) > MaxAcademicDocumentBytes {
		return AcademicDocumentFile{}, &Error{Code: "DOCUMENT_RESULT_UNKNOWN", Message: "证明文件过大或接收中断，请核对打印记录；不会自动重试"}
	}
	return AcademicDocumentFile{Body: body, ContentType: response.Header.Get("Content-Type")}, nil
}
