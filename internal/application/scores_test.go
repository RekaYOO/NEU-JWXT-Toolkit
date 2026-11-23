package application

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestMissingPolicyCodesOnlyRequestsUnresolvedCoursesOnce(t *testing.T) {
	scores := []academic.Score{{Code: "B2"}, {Code: "A1"}, {Code: "A1"}, {Code: "EXCLUDED"}, {Code: "KNOWN"}, {Code: "INLINE", GradingScale: "百分制"}, {Code: ""}}
	policy := map[string]any{"general_elective_codes": []string{"EXCLUDED"}, "grading_scales": map[string]string{"KNOWN": "两级制"}}
	if got := strings.Join(missingPolicyCodes(scores, policy), ","); got != "A1,B2" {
		t.Fatalf("unexpected missing courses: %s", got)
	}
}

func TestSavingModernPolicyWarmsOnlyMissingInputs(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20240001")
	ctx := context.Background()
	session, err := api.database.Session(ctx, cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	scores := json.RawMessage(`{"scores":[{"code":"A1"},{"code":"A1"},{"code":"B2","grading_scale":"百分制"}]}`)
	if _, err := api.database.CommitCache(ctx, session, store.CacheEntry{Resource: "scores", Variant: "default", Payload: scores, Revision: "scores-1", SchemaVersion: 1}, "test", nil); err != nil {
		t.Fatal(err)
	}
	for _, resource := range []string{"academic-report", "course-outline-metadata"} {
		api.resources.Register(resource, ResourceSpec{TTL: time.Hour, Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
			return json.RawMessage(`{"grading_scale":"五级制"}`), nil, nil
		}})
	}
	response := apiRequest(handler, "PUT", "/api/gpa-policy", `{"mode":"through_2024"}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	api.warmCachedScoreDetails(session)
	api.afterScoresRefresh(session, scores, "tracking")
	api.resources.mutex.Lock()
	var legacyJobs int
	for _, job := range api.resources.jobs {
		if job.Reason == "gpa_context" {
			legacyJobs++
		}
	}
	api.resources.mutex.Unlock()
	if legacyJobs != 0 {
		t.Fatalf("legacy policy scheduled %d GPA refresh jobs", legacyJobs)
	}
	response = apiRequest(handler, "PUT", "/api/gpa-policy", `{"mode":"from_2025"}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	api.resources.mutex.Lock()
	var jobs []*CacheJob
	for _, job := range api.resources.jobs {
		if job.Reason == "gpa_context" {
			jobs = append(jobs, job)
		}
	}
	api.resources.mutex.Unlock()
	if len(jobs) != 2 {
		t.Fatalf("expected a report and one missing course, got %d jobs", len(jobs))
	}
	for _, job := range jobs {
		select {
		case <-job.done:
		case <-time.After(5 * time.Second):
			t.Fatal("policy refresh timed out")
		}
	}
	for resource, variant := range map[string]string{"academic-report": "default", "course-outline-metadata": "course:A1"} {
		if _, err := api.database.ReadCache(ctx, session, resource, variant); err != nil {
			t.Fatal(resource, variant, err)
		}
	}
	api.warmCachedScoreDetails(session)
	api.afterScoresRefresh(session, scores, "tracking")
	api.resources.mutex.Lock()
	defer api.resources.mutex.Unlock()
	count := 0
	for _, job := range api.resources.jobs {
		if job.Reason == "gpa_context" {
			count++
		}
	}
	if count != len(jobs) {
		t.Fatalf("fresh inputs created %d extra GPA refresh jobs", count-len(jobs))
	}
}

func TestScoresPolicyAndCachedReportHTTP(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	scores := academic.Scores{Scores: []academic.Score{{Name: "必修", Code: "REQ", Credit: 4, GPA: 4, Term: "2025-1", DetailRef: "secret-reference"}, {Name: "选修", Code: "GEN", Credit: 2, GPA: 5, Term: "2025-1"}, {Name: "两级", Code: "PASS", Credit: 1, GPA: 4, Term: "2025-1", GradingScale: "两级制"}, {Name: "挂科", Code: "FAIL", Credit: 2, GPA: 0, Term: "2025-1"}}}
	payload, _ := json.Marshal(scores)
	report := json.RawMessage(`{"student_id":"20250001","categories":[{"name":"通识类","children":[{"name":"通识选修类","courses":[{"course_code":"GEN"}],"children":[]}]}],"credit_summary":{"total_required":9}}`)
	for _, entry := range []store.CacheEntry{{Resource: "scores", Variant: "default", Payload: payload, Revision: "scores-1", SchemaVersion: 1}, {Resource: "academic-report", Variant: "default", Payload: report, Revision: "report-1", SchemaVersion: 1}} {
		if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	response := apiRequest(handler, "GET", "/api/scores/cache", "", cookie)
	var result map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil || response.Code != 200 {
		t.Fatalf("cache response: %s %v", response.Body.String(), err)
	}
	if result["calculated_gpa"] != float64(16.0/6.0) || strings.Contains(response.Body.String(), "secret-reference") {
		t.Fatalf("policy/privacy: %s", response.Body.String())
	}
	response = apiRequest(handler, "PUT", "/api/gpa-policy", `{"mode":"through_2024"}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/scores/cache", "", cookie)
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &result) != nil || result["calculated_gpa"] != float64(30.0/9.0) {
		t.Fatalf("legacy policy: %s", response.Body.String())
	}
	response = apiRequest(handler, "PUT", "/api/gpa-policy", `{"mode":null}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/scores/cache", "", cookie)
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &result) != nil || result["calculated_gpa"] != float64(16.0/6.0) {
		t.Fatalf("restored year default: %s", response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/scores/by-term", "", cookie)
	var groups []map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &groups); err != nil || len(groups) != 1 || groups[0]["total_credits"] != float64(9) {
		t.Fatalf("term totals changed with policy: %s %v", response.Body.String(), err)
	}
	response = apiRequest(handler, "GET", "/api/academic-report/cache", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"total_required":9`) {
		t.Fatalf("report cache: %s", response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/academic-report/summary", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"category_summary"`) {
		t.Fatalf("report summary: %s", response.Body.String())
	}
	if response = apiRequest(handler, "GET", "/api/academic-report/cache", "", nil); response.Code != 401 {
		t.Fatal("report accessible without identity")
	}
}
