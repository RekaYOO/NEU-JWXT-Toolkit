package academic

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func TestJWXKDetailHTMLOnlyProjectsDisplayFields(t *testing.T) {
	result := ParseJWXKDetailHTML(`<table><tr><th>课程代码：</th><td>C1</td><th>课程名称</th><td>课程 <script>private script</script> 名称</td></tr><tr><th>学生姓名</th><td>private student</td></tr></table><dl><dt>英文名称</dt><dd>Test Course</dd></dl><label>课程简介</label><div>介绍 <b>内容</b></div>`)
	if result["course_code"] != "C1" || result["course_name"] != "课程 名称" || result["english_name"] != "Test Course" || result["description"] != "介绍 内容" {
		t.Fatal(result)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "private") {
		t.Fatal(result)
	}
}

func TestJWXKDetailOptionalDescriptionsAndExactClass(t *testing.T) {
	for _, failOptional := range []bool{false, true} {
		calls := 0
		requester := serviceRequestFunc(func(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
			calls++
			switch input.Path {
			case "/xsxk/elective/user":
				return []byte(`{"code":200,"data":{"student":{"electiveBatchList":[{"code":"round","typeCode":"04"}]}}}`), nil
			case "/xsxk/elective/clazz/list":
				return []byte(`{"code":200,"data":{"total":1,"rows":[{"KCH":"C1","KCM":"原名称","JXBID":"class","KRL":10,"YXRS":2,"secretVal":"private-token"}]}}`), nil
			case "/xsxk/web/kc/C1":
				if failOptional {
					return nil, &campus.Error{Code: "UPSTREAM_UNAVAILABLE"}
				}
				return []byte(`<table><tr><th>课程名称</th><td>课程详情名称</td><th>课程类别</th><td>通识选修类</td></tr></table>`), nil
			case "/xsxk/elective/ssfa":
				if input.ReadOnly || input.Form.Get("kch") != "C1" {
					t.Fatal(input)
				}
				if failOptional {
					return []byte(`{"code":500}`), nil
				}
				return []byte(`{"code":200,"data":[{"NJ":"2026","FA":"方案","secret":"private-program"},{"NJ":"2026","FA":"方案"}]}`), nil
			}
			t.Fatal(input)
			return nil, nil
		})
		input := JWXKDetail{Batch: "round", Scope: "FANKC", Course: "C1", Class: "class"}
		result, err := FetchJWXKDetail(context.Background(), requester, "direct", input)
		if err != nil || calls != 4 || result["programs_loaded"] == failOptional {
			t.Fatal(result, err, calls)
		}
		course := result["course"].(map[string]any)
		if !failOptional && (course["course_name"] != "课程详情名称" || course["normalized_course_category"] != "通识选修" || len(result["programs"].([]map[string]string)) != 1) {
			t.Fatal(result)
		}
		encoded, _ := json.Marshal(result)
		if strings.Contains(string(encoded), "private-") {
			t.Fatal("secret exposed")
		}
		input.Class = "different"
		if _, err := FetchJWXKDetail(context.Background(), requester, "direct", input); err == nil {
			t.Fatal("wrong class accepted")
		}
		if calls != 6 {
			t.Fatal("optional calls made for missing class", calls)
		}
	}
}
