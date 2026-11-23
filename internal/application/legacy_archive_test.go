package application

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestLegacyArchiveImportSupportsHistoryLocalSearchAndIsolation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	cookie := loginCookie(t, handler, "student-a")
	other := loginCookie(t, handler, "student-b")
	root := t.TempDir()
	writeLegacyFixture(t, root, "course_selection_catalog_history.json", `[
 {"account":"student-a","archive_id":"old-uuid","batch_code":"ROUND1","batch_name":"历史轮次","term_code":"2025-2026-1","archived":true,"catalog_complete":true,"sync_status":"running","sync_scopes":[{"page":3}],"secretVal":"never-copy","courses":[
 {"class_id":"CLASS1","course_code":"C1","course_name":"保留的课程","teaching_class_type":"FANKC","source_scopes":["FANKC"],"capacity":30,"selected_count":20,"secretVal":"never-copy","schedules":[{"weekday":1,"start_section":1,"end_section":2,"weeks":[1,3]}]},
 {"class_id":"CLASS2","course_code":"C2","course_name":"全校发现课程","teaching_class_type":"ALLKC","source_scopes":["ALLKC"],"capacity":50,"selected_count":12}]},
 {"account":"student-b","batch_code":"OTHER","term_code":"2025-2026-1","courses":[]}
 ]`)
	preview, err := ImportLegacy(ctx, api.database, root, "student-a", "")
	if err != nil || len(preview.Items) != 1 {
		t.Fatal(preview, err)
	}
	if _, err := ImportLegacy(ctx, api.database, root, "student-a", preview.Digest); err != nil {
		t.Fatal(err)
	}
	endpoint := "/api/course-selection/jwxk/catalog/archives/" + jwxkArchiveID("ROUND1")
	response := apiRequest(handler, "GET", endpoint, "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "保留的课程") || strings.Contains(response.Body.String(), "never-copy") || strings.Contains(response.Body.String(), "sync_scopes") {
		t.Fatal(response.Code, response.Body.String())
	}
	var archive map[string]any
	json.Unmarshal(response.Body.Bytes(), &archive)
	if archive["sync_status"] != "imported" || archive["archived"] != true || archive["catalog_complete"] != true {
		t.Fatal("history state lost", archive)
	}
	response = apiRequest(handler, "POST", "/api/course-selection/jwxk/catalog/search", `{"batch_code":"ROUND1","scope":"ROUND","local_only":true}`, cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "保留的课程") || strings.Contains(response.Body.String(), "全校发现课程") {
		t.Fatal("local scope semantics changed", response.Code, response.Body.String())
	}
	if response := apiRequest(handler, "GET", endpoint, "", other); response.Code != 404 {
		t.Fatal("archive leaked to another account")
	}
	if response := apiRequest(handler, "DELETE", endpoint, "", cookie); response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	if _, err := ImportLegacy(ctx, api.database, root, "student-a", preview.Digest); err == nil {
		t.Fatal("deleted history resurrected")
	}
}

func TestLegacyArchiveLargerThanOrdinaryDocumentLimit(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	ctx := context.Background()
	cookie := loginCookie(t, handler, "student")
	courses := []map[string]any{}
	for index := 0; index < 2400; index++ {
		courses = append(courses, map[string]any{"class_id": fmt.Sprintf("CLASS%d", index), "course_code": fmt.Sprintf("C%d", index), "course_name": "完整目录课程", "teaching_class_type": "FANKC", "notice": strings.Repeat("n", 1900)})
	}
	encoded, err := json.Marshal([]map[string]any{{"account": "student", "batch_code": "LARGE", "term_code": "2025-2026-1", "archived": true, "courses": courses}})
	if err != nil || len(encoded) <= 4*1024*1024 {
		t.Fatal("fixture does not cover previous size limit", err)
	}
	root := t.TempDir()
	writeLegacyFixture(t, root, "course_selection_catalog_history.json", string(encoded))
	preview, err := ImportLegacy(ctx, api.database, root, "student", "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ImportLegacy(ctx, api.database, root, "student", preview.Digest); err != nil {
		t.Fatal(err)
	}
	response := apiRequest(handler, "GET", "/api/course-selection/jwxk/catalog/archives/"+jwxkArchiveID("LARGE"), "", cookie)
	var result struct {
		Courses []map[string]any `json:"courses"`
	}
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &result) != nil || len(result.Courses) != 2400 {
		t.Fatal("large archive truncated", response.Code, len(result.Courses))
	}
	if _, err := api.database.Save(ctx, "student", "gpa", "oversized.json", encoded, 0, false); err == nil {
		t.Fatal("ordinary document limit widened")
	}
}

func TestLegacyArchiveNormalizesProvenanceAndRejectsDuplicateClasses(t *testing.T) {
	archive := map[string]any{"batch_code": "ROUND1", "term_code": "2025-2026-1", "courses": []any{
		map[string]any{"class_id": "CLASS1", "teaching_class_type": "ALLKC", "source_scopes": []any{"FANKC", "ALLKC", "FANKC"}},
		map[string]any{"class_id": "CLASS2"},
	}}
	converted, err := convertLegacyArchive(archive)
	if err != nil {
		t.Fatal(err)
	}
	rows := converted["courses"].([]map[string]any)
	if rows[0]["teaching_class_type"] != "FANKC" || len(rows[0]["source_scopes"].([]string)) != 2 || rows[1]["teaching_class_type"] != "ALLKC" {
		t.Fatal("source normalization changed", rows)
	}
	archive["courses"] = []any{map[string]any{"class_id": "duplicate"}, map[string]any{"class_id": "duplicate"}}
	if _, err := convertLegacyArchive(archive); err == nil {
		t.Fatal("duplicate classes accepted")
	}
}
