package academic

import (
	"context"
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

var courseCodePattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,64}$`)

func ValidCourseCode(code string) bool { return courseCodePattern.MatchString(code) }

func FetchOutlineMetadata(ctx context.Context, requester Requester, code string) (map[string]any, error) {
	if !ValidCourseCode(code) {
		return nil, &campus.Error{Code: "INVALID_COURSE_CODE", Message: "课程代码无效"}
	}
	post := func(path string) (map[string]any, error) {
		encoded, err := requester.Request(ctx, http.MethodPost, "https://jwxt.neu.edu.cn/jwapp/sys/kccx/"+path, url.Values{"KCH": {code}})
		if err != nil {
			return nil, err
		}
		payload, err := Decode(encoded)
		if err != nil {
			return nil, err
		}
		if value, exists := payload["code"]; exists && Text(value) != "0" {
			return nil, &campus.Error{Code: "OUTLINE_RESPONSE_REJECTED", Message: "学校未确认大纲查询成功"}
		}
		return payload, nil
	}
	if _, err := post("api/kcdgwhgl/cshdgsj.do"); err != nil {
		return nil, err
	}
	payload, err := post("modules/kcdgwhgl/cxkcxxx.do")
	if err != nil {
		return nil, err
	}
	container := payload
	if nested := Object(Object(payload["datas"])["cxkcxxx"]); nested != nil {
		container = nested
	}
	var rows []map[string]any
	if row := Object(container["rows"]); row != nil {
		rows = []map[string]any{row}
	} else {
		if container["rows"] == nil {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "课程大纲基本信息结构异常"}
		}
		rows, err = reportRows(container["rows"])
		if err != nil {
			return nil, err
		}
	}
	basic := map[string]any{}
	if len(rows) > 0 {
		basic = rows[0]
	}
	display := func(field string) string {
		return strings.TrimSpace(firstText(basic[field+"_DISPLAY"], basic[field+"DISPLAY"], basic[field]))
	}
	result := map[string]any{"course_code": code, "course_name": display("KCM"), "assessment_method_code": Text(basic["KSLXDM"]), "assessment_method": display("KSLXDM"), "grading_scale_code": Text(basic["CJJLFS"]), "grading_scale": display("CJJLFS"), "outline_version": firstText(basic["BBWID"], basic["WID"]), "status": "not_found"}
	for _, field := range []string{"course_name", "assessment_method", "grading_scale", "outline_version"} {
		if result[field] != "" {
			result["status"] = "success"
			break
		}
	}
	return result, nil
}
