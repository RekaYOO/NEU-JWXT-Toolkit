package campus

import (
	"context"
	"net/http"
	"net/url"
	"strings"
)

type ServiceFile struct {
	Body        []byte
	ContentType string
	URL         string
}

func (client *Client) DownloadService(ctx context.Context, mode, path string, normalize func(string) string) (ServiceFile, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if normalize == nil || mode != "direct" && mode != "webvpn" {
		return ServiceFile{}, serviceFailure("INVALID_REQUEST", "证书下载参数无效")
	}
	target := normalize(path)
	if target == "" {
		return ServiceFile{}, serviceFailure("INVALID_REQUEST", "证书地址不在允许目录")
	}
	if active, _ := client.Status(); !active {
		return ServiceFile{}, serviceFailure("SERVICE_NOT_SENT", "请先恢复学校登录")
	}
	if err := client.ensureService(ctx, "cxcy", mode, false); err != nil {
		return ServiceFile{}, err
	}
	recovered := false
	for hops := 0; hops < 10; hops++ {
		wire, err := routeServiceURL(target, mode)
		if err != nil {
			return ServiceFile{}, err
		}
		body, headers, status, err := client.serviceExchangeBounded(ctx, http.MethodGet, wire, nil, http.Header{"Accept": {"image/png,image/jpeg,image/webp"}}, 10*1024*1024)
		if err != nil {
			return ServiceFile{}, err
		}
		authRequired := serviceNeedsAuth("cxcy", "", status, body) && !(status >= 300 && status < 400)
		if status >= 300 && status < 400 {
			base, _ := url.Parse(target)
			reference, err := url.Parse(headers.Get("Location"))
			if err != nil || reference == nil || headers.Get("Location") == "" || strings.ContainsAny(reference.Path, "\\%") {
				return ServiceFile{}, serviceFailure("UNTRUSTED_REDIRECT", "证书跳转地址无效")
			}
			for _, segment := range strings.Split(reference.Path, "/") {
				if segment == "." || segment == ".." {
					return ServiceFile{}, serviceFailure("UNTRUSTED_REDIRECT", "证书跳转路径无效")
				}
			}
			next := base.ResolveReference(reference)
			if canonical := normalize(next.String()); canonical != "" {
				target = canonical
				continue
			}
			for _, origin := range []string{"https://pass.neu.edu.cn", "https://cxcy.neu.edu.cn", "https://webvpn.neu.edu.cn"} {
				if restored, err := RestoreServiceURL(next.String(), origin); err == nil {
					parsed, _ := url.Parse(restored)
					if origin == "https://pass.neu.edu.cn" && strings.HasPrefix(parsed.Path, "/tpass/") || origin == "https://cxcy.neu.edu.cn" && strings.HasPrefix(parsed.Path, "/ucenter/") || origin == "https://webvpn.neu.edu.cn" && strings.HasPrefix(parsed.Path, "/login") {
						authRequired = true
					}
				}
			}
			if !authRequired {
				return ServiceFile{}, serviceFailure("UNTRUSTED_REDIRECT", "证书跳转离开允许目录")
			}
		}
		if authRequired {
			client.serviceReady["cxcy:"+mode] = false
			if recovered {
				return ServiceFile{}, serviceFailure("SERVICE_AUTH_REQUIRED", "创院系统登录已失效")
			}
			recovered = true
			if err := client.ensureService(ctx, "cxcy", mode, true); err != nil {
				return ServiceFile{}, err
			}
			continue
		}
		if status < 200 || status >= 300 {
			return ServiceFile{}, serviceFailure("UPSTREAM_HTTP_ERROR", "证书暂时无法下载")
		}
		return ServiceFile{Body: body, ContentType: headers.Get("Content-Type"), URL: target}, nil
	}
	return ServiceFile{}, serviceFailure("UNTRUSTED_REDIRECT", "证书跳转次数过多")
}
