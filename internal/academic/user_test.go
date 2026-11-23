package academic

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/url"
	"strings"
	"testing"
)

func TestAvatarDownloadContracts(t *testing.T) {
	image := []byte("GIF89a\x01\x00\x01\x00")
	for _, direct := range []bool{true, false} {
		calls := 0
		requester := requestFunc(func(_ context.Context, method, target string, _ url.Values) ([]byte, error) {
			calls++
			if calls == 1 {
				if method != "POST" || !strings.HasSuffix(target, "currentUser.do") {
					t.Fatal(target)
				}
				return []byte(`{"code":"0","datas":{"userId":"student-a","userName":"同学","avatarToken":"photo"}}`), nil
			}
			if calls == 2 && !direct {
				return []byte(`{"items":[{"fileUrl":"/photos/avatar.gif"}]}`), nil
			}
			if calls == 3 && target != "https://jwxt.neu.edu.cn/photos/avatar.gif" {
				t.Fatal(target)
			}
			return image, nil
		})
		result, err := FetchAvatar(context.Background(), requester, "student-a")
		if err != nil || result["image_base64"] != base64.StdEncoding.EncodeToString(image) || result["media_type"] != "image/gif" {
			t.Fatalf("%v %v", result, err)
		}
	}
}

func TestAvatarRejectsUnsafeReferencesBeforeDownload(t *testing.T) {
	for _, reference := range []string{"", " ", "https://outside.example/image", "//outside.example/image", "http://jwxt.neu.edu.cn/image", "https://user@jwxt.neu.edu.cn/image", "/image#fragment"} {
		t.Run(reference, func(t *testing.T) {
			calls := 0
			requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
				calls++
				if calls == 1 {
					return []byte(`{"code":"0","datas":{"userId":"student-a","avatarToken":"photo"}}`), nil
				}
				payload, _ := json.Marshal(map[string]any{"items": []any{map[string]any{"fileUrl": reference}}})
				return payload, nil
			})
			if _, err := FetchAvatar(context.Background(), requester, "student-a"); err == nil || calls != 2 {
				t.Fatalf("err=%v calls=%d", err, calls)
			}
		})
	}
}

func TestAvatarIdentityAndContentBoundaries(t *testing.T) {
	requester := requestFunc(func(context.Context, string, string, url.Values) ([]byte, error) {
		return []byte(`{"code":"0","datas":{"userId":"other","avatarToken":"photo"}}`), nil
	})
	if _, err := FetchUserInfo(context.Background(), requester, "student-a"); err == nil {
		t.Fatal("identity mismatch accepted")
	}
	for _, image := range [][]byte{[]byte(`<svg xmlns="http://www.w3.org/2000/svg"></svg>`), []byte("<html>login</html>"), append([]byte("GIF89a"), make([]byte, 4*1024*1024)...)} {
		if avatarMedia(image) != "" {
			t.Fatal("unsafe image accepted")
		}
	}
}
