package campus

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

func TestServiceDownloadChecksRedirectsHeadersAndSize(t *testing.T) {
	for _, scenario := range []string{"direct", "redirect", "escape", "traversal", "oversize", "webvpn"} {
		t.Run(scenario, func(t *testing.T) {
			client, _ := NewClient("student")
			mode := "direct"
			if scenario == "webvpn" {
				mode = "webvpn"
			}
			client.setStatus(true, mode)
			client.serviceReady = map[string]bool{"cxcy:" + mode: true}
			calls := 0
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				calls++
				if scenario == "webvpn" && request.URL.Hostname() != vpnHost {
					t.Fatal("download used wrong route")
				}
				if calls == 1 && (scenario == "redirect" || scenario == "escape" || scenario == "traversal") {
					response := mockResponse(request, 302, "")
					target := "/static/uploads/res/popsciencecert/second.png"
					if scenario == "escape" {
						target = "https://evil.invalid/image.png"
					}
					if scenario == "traversal" {
						target = "/static/uploads/res/popsciencecert/x/../second.png"
					}
					response.Header.Set("Location", target)
					return response, nil
				}
				response := mockResponse(request, 200, "\x89PNG\r\n\x1a\nsynthetic")
				response.Header.Set("Content-Type", "image/png")
				if scenario == "oversize" {
					response.ContentLength = 10*1024*1024 + 1
				}
				return response, nil
			})
			normalize := func(value string) string {
				if restored, err := RestoreServiceURL(value, "https://cxcy.neu.edu.cn"); err == nil && strings.HasPrefix(restored, "https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/") {
					return restored
				}
				return ""
			}
			file, err := client.DownloadService(context.Background(), mode, "https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/first.png", normalize)
			if scenario == "escape" || scenario == "traversal" || scenario == "oversize" {
				if err == nil || calls != 1 {
					t.Fatal("unsafe download accepted", file, err, calls)
				}
			} else if err != nil || file.ContentType != "image/png" {
				t.Fatal(file, err)
			}
			if scenario == "redirect" && (calls != 2 || !strings.HasSuffix(file.URL, "second.png")) {
				t.Fatal(file, calls)
			}
		})
	}
}

func TestServiceDownloadRecoversOnlyOnceWithoutRevokingPrimary(t *testing.T) {
	for _, recovers := range []bool{false, true} {
		client, _ := NewClient("student")
		client.setStatus(true, "direct")
		client.serviceReady = map[string]bool{"cxcy:direct": true}
		images, handshakes := 0, 0
		client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
			if request.URL.Path == "/ucenter/auth/caslogin" {
				handshakes++
				response := mockResponse(request, 302, "")
				response.Header.Set("Location", "/popscience/comp/ucenter/main/index")
				return response, nil
			}
			if strings.Contains(request.URL.Path, "/comp/ucenter/") {
				return mockResponse(request, 200, "authenticated home"), nil
			}
			images++
			if images == 1 || !recovers {
				return mockResponse(request, 401, "expired"), nil
			}
			response := mockResponse(request, 200, "\x89PNG\r\n\x1a\n")
			response.Header.Set("Content-Type", "image/png")
			return response, nil
		})
		target := "https://cxcy.neu.edu.cn/static/uploads/res/popsciencecert/a.png"
		_, err := client.DownloadService(context.Background(), "direct", target, func(value string) string {
			if value == target {
				return value
			}
			return ""
		})
		if recovers && err != nil || !recovers && err == nil || images != 2 || handshakes != 1 {
			t.Fatal(recovers, err, images, handshakes)
		}
		if active, _ := client.Status(); !active {
			t.Fatal("certificate auth revoked primary")
		}
	}
}
