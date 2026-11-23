package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func writeLegacyFixture(t *testing.T, root, name, body string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
}

func legacyHash(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func TestImportedGPAPolicyAtYearBoundary(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	root := t.TempDir()
	for _, sample := range []struct{ account, fixture, expected, defaultMode string }{
		{"20240001", `{"mode":null}`, "through_2024", "through_2024"},
		{"20250001", `{"mode":null}`, "from_2025", "from_2025"},
		{"20250002", `{"mode":"through_2024"}`, "through_2024", "from_2025"},
	} {
		cookie := loginCookie(t, handler, sample.account)
		writeLegacyFixture(t, root, filepath.Join("gpa_preferences", legacyHash(sample.account)+".json"), sample.fixture)
		preview, err := ImportLegacy(context.Background(), api.database, root, sample.account, "")
		if err != nil {
			t.Fatal(err)
		}
		result, err := ImportLegacy(context.Background(), api.database, root, sample.account, preview.Digest)
		if err != nil || result.Applied != 1 {
			t.Fatal(result, err)
		}
		response := apiRequest(handler, "GET", "/api/gpa-policy", "", cookie)
		var policy map[string]any
		if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &policy) != nil || policy["mode"] != sample.expected || policy["default_mode"] != sample.defaultMode {
			t.Fatal(sample.account, response.Body.String())
		}
		value, present := policy["override"]
		if !present || (sample.expected == sample.defaultMode && value != nil) || (sample.expected != sample.defaultMode && value != sample.expected) {
			t.Fatal(sample.account, value)
		}
	}
}

func TestLegacyImportPreviewsAndPreservesAccountBoundWorkflows(t *testing.T) {
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
	owner := legacyHash("student-a")
	planPath := filepath.Join("成绩", "gpa_simulations", owner[:24], "我的方案.json")
	plan := `{"courses":[],"export_info":{"exported_by":"student-a","version":"2.0"},"stats":{"count":0}}`
	writeLegacyFixture(t, root, planPath, plan)
	writeLegacyFixture(t, root, filepath.Join("成绩", "其他账号.json"), `{"courses":[],"export_info":{"exported_by":"student-b"}}`)
	writeLegacyFixture(t, root, filepath.Join("gpa_preferences", owner+".json"), `{"mode":"from_2025"}`)
	writeLegacyFixture(t, root, "research_training_favorites.json", `{"version":1,"users":{"student-a":{"records":[{"batch_id":"B1","batch_name":"批次一","topic_id":"T1","topic":{"topic_id":"T1","title":"课题一","contact":"do-not-import"},"favorited_at":"2026-11-16T00:00:00Z"}]},"student-b":{"records":[{"batch_id":"B2","topic_id":"T2","topic":{"topic_id":"T2"}}]}}}`)
	writeLegacyFixture(t, root, "config.json", `{"course_selection_plans":{"student-a:B1":{"batch_code":"B1","term_code":"2026-2027-1","groups":[],"items":[{"course_code":"C1","class_id":"J1","priority":1}],"experiment_selections":{}},"student-b:B2":{"batch_code":"B2","term_code":"2026-2027-1","items":[]}},"password":"not-imported"}`)
	term := "2026-2027-1"
	writeLegacyFixture(t, root, filepath.Join("timetable_agendas", owner, legacyHash(term)+".json"), `{"revision":3,"events":[{"id":"event1","selection_mode":"date","date":"2099-11-16","title":"自定义日程","start_time":"08:00","end_time":"09:00"}],"moves":[]}`)
	preview, err := ImportLegacy(ctx, api.database, root, "student-a", "", term)
	if err != nil || len(preview.Items) != 5 || preview.Applied != 0 {
		t.Fatal(preview, err)
	}
	if _, err := api.database.Read(ctx, "student-a", "gpa", "我的方案.json"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("preview changed target")
	}
	applied, err := ImportLegacy(ctx, api.database, root, "student-a", preview.Digest, term)
	if err != nil || applied.Applied != 5 {
		t.Fatal(applied, err)
	}
	repeat, err := ImportLegacy(ctx, api.database, root, "student-a", preview.Digest, term)
	if err != nil || repeat.Applied != 0 {
		t.Fatal("not idempotent", repeat, err)
	}
	for _, item := range repeat.Items {
		if item.Status != "unchanged" {
			t.Fatal(item)
		}
	}
	if original, err := os.ReadFile(filepath.Join(root, planPath)); err != nil || string(original) != plan {
		t.Fatal("modified source", err)
	}
	for _, request := range []struct{ method, path, body, contains string }{
		{"GET", "/api/gpa-simulation/file/我的方案.json", "", `"courses":[]`},
		{"GET", "/api/gpa-policy", "", `"mode":"from_2025"`},
		{"GET", "/api/research-training/cache", "", "课题一"},
		{"POST", "/api/course-selection/jwxk/plan/read", `{"batch_code":"B1"}`, `"course_code":"C1"`},
		{"GET", "/api/timetable/agenda?term_code=" + term, "", "自定义日程"},
	} {
		response := apiRequest(handler, request.method, request.path, request.body, cookie)
		if response.Code != 200 || !strings.Contains(response.Body.String(), request.contains) {
			t.Fatal(request.path, response.Code, response.Body.String())
		}
		if strings.Contains(response.Body.String(), "do-not-import") {
			t.Fatal("private contact imported")
		}
	}
	if response := apiRequest(handler, "GET", "/api/gpa-simulation/files", "", other); response.Code != 200 || strings.Contains(response.Body.String(), "我的方案") {
		t.Fatal("cross account leak")
	}
}

func TestLegacyImportConflictsTombstonesAndChangedSourceAreAtomic(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	ctx := context.Background()
	loginCookie(t, handler, "student")
	root := t.TempDir()
	folder := filepath.Join("成绩", "gpa_simulations", legacyHash("student")[:24])
	writeLegacyFixture(t, root, filepath.Join(folder, "old.json"), `{"courses":[],"export_info":{"exported_by":"student"}}`)
	writeLegacyFixture(t, root, filepath.Join(folder, "new.json"), `{"courses":[],"export_info":{"exported_by":"student"}}`)
	preview, err := ImportLegacy(ctx, api.database, root, "student", "")
	if err != nil {
		t.Fatal(err)
	}
	writeLegacyFixture(t, root, filepath.Join(folder, "new.json"), `{"courses":[],"export_info":{"exported_by":"student"},"name":"changed"}`)
	if _, err := ImportLegacy(ctx, api.database, root, "student", preview.Digest); err == nil {
		t.Fatal("stale preview accepted")
	}
	if _, err := api.database.Save(ctx, "student", "gpa", "old.json", json.RawMessage(`null`), 0, true); err != nil {
		t.Fatal(err)
	}
	preview, err = ImportLegacy(ctx, api.database, root, "student", "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ImportLegacy(ctx, api.database, root, "student", preview.Digest); err == nil {
		t.Fatal("tombstone overwritten")
	}
	if _, err := api.database.Read(ctx, "student", "gpa", "new.json"); err != store.ErrMissing {
		t.Fatal("partially imported despite conflict")
	}
	writeLegacyFixture(t, root, filepath.Join(folder, "old.json"), `{"courses":[],"export_info":{"exported_by":"other"}}`)
	if _, err := ImportLegacy(ctx, api.database, root, "student", ""); err == nil {
		t.Fatal("wrong owner accepted")
	}
}
