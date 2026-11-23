package academic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"testing"
)

func reportFixtureRequester(t *testing.T, report string) Requester {
	t.Helper()
	return requestFunc(func(_ context.Context, method, target string, form url.Values) ([]byte, error) {
		if method != http.MethodPost || target != "https://jwxt.neu.edu.cn/jwapp/sys/byshapp/api/grbg/queryXyzhbx.do" || form.Get("fromPage") != "grxyjcbg" || form.Get("SCLX") != "04" || form.Get("XDLX") != "01" || form.Has("PYFADM") {
			t.Fatalf("unexpected report request: %s %s %v", method, target, form)
		}
		return []byte(report), nil
	})
}

func TestReportDualConstraintAndModernFields(t *testing.T) {
	payload := `{"code":"0","datas":{"queryXyzhbx":{"calculatedTime":"today","fanbx":{"name":"2025 测试专业","educationalProgramCode":"P25","children":[{"name":"通识选修","creditsRequired":8,"children":[{"name":"科学","creditsRequired":2,"checkCourseVOS":[{"courseName":"科学课程","courseId":"SCI","courseNature":"02","credit":6,"passed":true,"studentId":"20250001","studentName":"样例"}]},{"name":"艺术","creditsRequired":2,"checkCourseVOS":[{"courseName":"艺术课程","courseId":"ART","credit":2,"status":"03"}]}]}]},"fawbx":[{"courseName":"方案外","courseId":"OUT","credit":1,"passed":true}]}}}`
	report, err := FetchReport(context.Background(), reportFixtureRequester(t, payload), "20250001")
	if err != nil {
		t.Fatal(err)
	}
	if report["program_name"] != "2025 测试专业" || report["grade"] != "2025" || report["student_name"] != "样例" {
		t.Fatalf("identity/program: %+v", report)
	}
	summary := report["credit_summary"].(map[string]any)
	if summary["total_required"] != float64(8) || summary["total_passed"] != float64(6) || summary["total_selected"] != float64(2) || summary["total_remaining"] != float64(0) {
		t.Fatalf("summary: %+v", summary)
	}
	parent := report["categories"].([]map[string]any)[0]
	if parent["requires_child_minimums_and_total"] != true || parent["is_completed"] != true {
		t.Fatalf("dual constraint: %+v", parent)
	}
	children := parent["children"].([]map[string]any)
	if children[0]["earned_credits"] != float64(6) || children[0]["required_credits"] != float64(2) {
		t.Fatalf("child minimum changed: %+v", children[0])
	}
	outside := report["outside_courses"].([]map[string]any)
	if len(outside) != 1 || outside[0]["course_code"] != "OUT" {
		t.Fatalf("outside: %+v", outside)
	}
	before, err := ReportSemantic(report)
	if err != nil {
		t.Fatal(err)
	}
	report["calculated_time"] = "tomorrow"
	children[0], children[1] = children[1], children[0]
	after, err := ReportSemantic(report)
	if err != nil || string(before) != string(after) {
		t.Fatal("timestamp or ordering changed semantic revision")
	}
}

func TestReportRequiresEveryChildAndOfficialRules(t *testing.T) {
	for _, flag := range []string{`false`, `true`} {
		payload := `{"code":"0","datas":{"queryXyzhbx":{"fanbx":{"children":[{"name":"选修","creditsRequired":8,"children":[{"name":"科学","creditsRequired":2,"passed":` + flag + `,"checkCourseVOS":[{"credit":8,"passed":true}]},{"name":"艺术","creditsRequired":2,"checkCourseVOS":[]}]}]}}}}`
		report, err := FetchReport(context.Background(), reportFixtureRequester(t, payload), "20240001")
		if err != nil {
			t.Fatal(err)
		}
		parent := report["categories"].([]map[string]any)[0]
		if parent["is_completed"] != false || parent["aggregate_remaining_credits"] != float64(0) {
			t.Fatalf("excess credits hid unmet child: %+v", parent)
		}
	}
}

func TestReportCoursePreservesDistinctTermsAndRetake(t *testing.T) {
	for _, row := range []map[string]any{
		{"courseName": "合成课程", "status": "02", "retakeType": "02", "pointSchoolYearTermCode": "成绩期", "courseSelectionSchoolYearTermCode": "选课期", "schoolYearTerms": "计划期"},
		{"KCM": "合成课程", "ZT_DISPLAY": "挂科", "CXCKDM_DISPLAY": "重修", "CJXNXQDM_DISPLAY": "成绩期", "XKXNXQDM_DISPLAY": "选课期", "JHXNXQDM_DISPLAY": "计划期"},
	} {
		course := reportCourse(row, nil, "")
		for key, want := range map[string]string{"status": "挂科", "retake_status": "重修", "score_term_code": "成绩期", "select_term_code": "选课期", "plan_term_code": "计划期"} {
			if course[key] != want { t.Errorf("%s: got %v, want %s", key, course[key], want) }
		}
	}
}

func TestReportRejectsMissingBodyAndMixedIdentity(t *testing.T) {
	for _, payload := range []string{`{"code":"0","datas":{"queryXyzhbx":{}}}`, `{"code":"0","datas":{"queryXyzhbx":{"fanbx":{"children":{}}}}}`, `{"code":"0","datas":{"queryXyzhbx":{"fanbx":{"children":[{"checkCourseVOS":[{"studentId":"20240001"},{"studentId":"20240002"}]}]}}}}`} {
		if _, err := FetchReport(context.Background(), reportFixtureRequester(t, payload), "20240001"); err == nil {
			t.Fatalf("accepted invalid report: %s", payload)
		}
	}
	payload := `{"code":"0","datas":{"queryXyzhbx":{"fanbx":{"educationalProgramName":"旧方案","children":[]},"fawbxMap":[{"KCM":"旧课程","KCH":"OLD","XF":2,"SFJG_DISPLAY":"是","XH":"20240001"}]}}}`
	report, err := FetchReport(context.Background(), reportFixtureRequester(t, payload), "20240001")
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(report)
	if report["program_name"] != "旧方案" || report["outside_courses"].([]map[string]any)[0]["is_passed"] != true {
		t.Fatalf("legacy fields: %s", encoded)
	}
}

func TestExportReportKeepsOriginalFieldsOutsideCache(t *testing.T) {
	const payload = `{"code":"0","datas":{"queryXyzhbx":{"officialOnly":"original","fanbx":{"children":[]}}}}`
	normalized, original, err := FetchReportForExport(context.Background(), reportFixtureRequester(t, payload), "20240001")
	if err != nil || original["officialOnly"] != "original" || normalized["officialOnly"] != nil {
		t.Fatal("original and normalized report must remain separate", err)
	}
	const mixed = `{"code":"0","datas":{"queryXyzhbx":{"fanbx":{"children":[]},"fawbx":[{"studentId":"20240002"}]}}}`
	normalized, original, err = FetchReportForExport(context.Background(), reportFixtureRequester(t, mixed), "20240001")
	if err == nil || original != nil || normalized != nil { t.Fatal("mixed-account report returned for export") }
}
