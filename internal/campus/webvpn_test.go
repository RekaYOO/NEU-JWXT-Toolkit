package campus

import "testing"

func TestWebVPNMatchesLegacyProtocol(t *testing.T) {
	actual, err := WebVPNURL("https://jwxt.neu.edu.cn:443/a%20b?x=1#tail")
	expected := "https://webvpn.neu.edu.cn/https-443/62304135386136393339346365373340baf6bc2bc4cb43c8bc1d6f66c806db/a%20b?x=1#tail"
	if err != nil || actual != expected {
		t.Fatalf("protocol mismatch: %q %v", actual, err)
	}
}

func TestWebVPNRoundTrip(t *testing.T) {
	origin := "https://cxcy.neu.edu.cn"
	for _, path := range []string{"/popscience/comp/ucenter/main/index?foo=1#details", "/static/uploads/%E8%AF%81%E6%98%8E.png"} {
		converted, err := WebVPNURL(origin + path)
		if err != nil {
			t.Fatal(err)
		}
		restored, err := RestoreServiceURL(converted, origin)
		if err != nil || restored != origin+path {
			t.Fatalf("round trip: %q, %v", restored, err)
		}
	}
}

func TestWebVPNRejectsUntrustedService(t *testing.T) {
	origin := "https://cxcy.neu.edu.cn"
	otherService, _ := WebVPNURL("https://jwxt.neu.edu.cn/secret")
	for _, raw := range []string{
		"https://evil.example/file", "//evil.example/file", "http://cxcy.neu.edu.cn/file",
		"https://user:password@cxcy.neu.edu.cn/file", "/safe/../secret", "/safe/%2e%2e/secret",
		"/safe/%252e%252e/secret", "/safe\\secret", otherService,
	} {
		if _, err := RestoreServiceURL(raw, origin); err == nil {
			t.Fatalf("accepted unsafe URL %q", raw)
		}
	}
}
