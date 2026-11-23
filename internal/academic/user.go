package academic

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

var avatarTokenPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,160}$`)

func FetchUserInfo(ctx context.Context, requester Requester, account string) (map[string]any, error) {
	datas, err := requestData(ctx, requester, http.MethodPost, homeBase+"currentUser.do", url.Values{})
	if err != nil {
		return nil, err
	}
	id := strings.TrimSpace(Text(datas["userId"]))
	if id == "" || id != account {
		return nil, &campus.Error{Code: "ACADEMIC_IDENTITY_MISMATCH", Message: "学校用户资料与当前账号不一致"}
	}
	token := Text(datas["avatarToken"])
	target := ""
	if token != "" && avatarTokenPattern.MatchString(token) && token != "." && token != ".." {
		target = "https://jwxt.neu.edu.cn/jwapp/sys/emapcomponent/file/getUploadedAttachment/" + token + ".do"
	} else {
		token = ""
	}
	return map[string]any{"user_name": Text(datas["userName"]), "user_id": id, "avatar_token": token, "avatar_url": target, "default_avatar": Text(datas["userImg"])}, nil
}

func avatarMedia(payload []byte) string {
	if len(payload) == 0 || len(payload) > 4*1024*1024 {
		return ""
	}
	kind := http.DetectContentType(payload)
	switch kind {
	case "image/png", "image/jpeg", "image/gif", "image/webp":
		return kind
	}
	return ""
}

func FetchAvatar(ctx context.Context, requester Requester, account string) (map[string]any, error) {
	profile, err := FetchUserInfo(ctx, requester, account)
	if err != nil {
		return nil, err
	}
	if profile["avatar_url"] == "" {
		return nil, &campus.Error{Code: "AVATAR_UNAVAILABLE", Message: "当前账号暂无头像"}
	}
	payload, err := requester.Request(ctx, http.MethodGet, Text(profile["avatar_url"]), nil)
	if err != nil {
		return nil, err
	}
	media := avatarMedia(payload)
	if media == "" {
		metadata, err := Decode(payload)
		if err != nil {
			return nil, err
		}
		items, err := reportRows(metadata["items"])
		if err != nil || len(items) == 0 {
			return nil, &campus.Error{Code: "AVATAR_UNAVAILABLE", Message: "学校未返回有效头像"}
		}
		base, _ := url.Parse("https://jwxt.neu.edu.cn/")
		fileURL := strings.TrimSpace(Text(items[0]["fileUrl"]))
		reference, err := url.Parse(fileURL)
		if err != nil || fileURL == "" {
			return nil, &campus.Error{Code: "UNTRUSTED_URL", Message: "头像下载地址无效"}
		}
		target := base.ResolveReference(reference)
		if target.Scheme != "https" || target.Host != "jwxt.neu.edu.cn" || target.User != nil || target.Fragment != "" {
			return nil, &campus.Error{Code: "UNTRUSTED_URL", Message: "头像下载地址不属于学校服务"}
		}
		payload, err = requester.Request(ctx, http.MethodGet, target.String(), nil)
		if err != nil {
			return nil, err
		}
		media = avatarMedia(payload)
	}
	if media == "" {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校头像格式或大小不受支持"}
	}
	return map[string]any{"image_base64": base64.StdEncoding.EncodeToString(payload), "media_type": media, "token": profile["avatar_token"]}, nil
}
