package application

import (
	"context"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

type outlineTestClient struct {
	testCampusClient
	calls atomic.Int32
}

func (client *outlineTestClient) Request(_ context.Context, method, target string, _ url.Values) ([]byte, error) {
	client.calls.Add(1)
	if method == "GET" && strings.HasSuffix(target, "/getUploadedAttachment/safe-token.do") {
		return []byte("anonymous attachment"), nil
	}
	return []byte(`{"rows":[]}`), nil
}

func TestOutlineAttachmentBoundaryAndNoPersistence(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	client := &outlineTestClient{testCampusClient: testCampusClient{account: "20250001"}}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "20250001")
	for _, token := range []string{"../private", "https://other.test/file", "..", "safe%2ftoken"} {
		response := apiRequest(handler, "POST", "/api/course-outlines/attachments/download", `{"token":"`+token+`"}`, cookie)
		if response.Code != 422 {
			t.Fatalf("unsafe token accepted: %s", token)
		}
	}
	if client.calls.Load() != 0 {
		t.Fatal("invalid attachment reached upstream")
	}
	response := apiRequest(handler, "POST", "/api/course-outlines/attachments/download", `{"token":"safe-token","filename":"测试 附件.pdf"}`, cookie)
	if response.Code != 200 || response.Body.String() != "anonymous attachment" || response.Header().Get("Cache-Control") != "no-store" || !strings.Contains(response.Header().Get("Content-Disposition"), "%20") {
		t.Fatalf("download: %d %v %s", response.Code, response.Header(), response.Body.String())
	}
	if len(api.resources.jobs) != 0 {
		t.Fatal("attachment entered persistent resource path")
	}
	response = apiRequest(handler, "POST", "/api/course-outlines/detail/sections", `{"course_code":"TEST","group":"teaching"}`, cookie)
	if response.Code != 200 || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "POST", "/api/course-outlines/detail/sections", `{"course_code":"TEST","group":"arbitrary"}`, cookie)
	if response.Code != 422 {
		t.Fatal("unknown section accepted")
	}
}

var _ CampusClient = (*outlineTestClient)(nil)
