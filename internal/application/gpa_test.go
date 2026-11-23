package application

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGPAPlansWithSameFilenameRemainAccountScoped(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		api.database.AddAccount(context.Background(), account)
	}
	first := loginCookie(t, handler, "student-a")
	second := loginCookie(t, handler, "student-b")
	const save = "/api/gpa-simulation/export"
	const read = "/api/gpa-simulation/file/shared.json"
	const plan = `{"filename":"shared","revision":0,"data":{"courses":[]}}`
	if response := apiRequest(handler, "POST", save, plan, first); response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	if response := apiRequest(handler, "GET", read, "", second); response.Code != 404 {
		t.Fatalf("other account read returned %d", response.Code)
	}
	if response := apiRequest(handler, "POST", save, plan, second); response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	if response := apiRequest(handler, "POST", "/api/gpa-simulation/rename", `{"filename":"shared","new_filename":"moved","revision":1}`, first); response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response := apiRequest(handler, "GET", read, "", second)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"exported_by":"student-b"`) || response.Header().Get("ETag") != `"1"` {
		t.Fatal("renaming first account plan changed second account plan")
	}
	request := httptest.NewRequest("DELETE", read, nil)
	request.AddCookie(second)
	request.Header.Set("If-Match", response.Header().Get("ETag"))
	deleted := httptest.NewRecorder()
	handler.ServeHTTP(deleted, request)
	if deleted.Code != 200 || apiRequest(handler, "GET", read, "", second).Code != 404 {
		t.Fatal("delete with the returned entity tag did not remove the plan")
	}
}

func TestGPAFilesRoundTripAtomicRenameAndConflict(t *testing.T) {
	_, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	response := apiRequest(handler, "POST", "/api/gpa-simulation/export", `{"filename":"plan","revision":0,"data":{"courses":[{"name":"course","credit":2,"gpa":3.5}],"export_info":{"exported_by":"someone-else"}}}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/gpa-simulation/file/plan.json", "", cookie)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"exported_by":"20250001"`) || !strings.Contains(response.Body.String(), `"_document_revision":1`) {
		t.Fatal("owner metadata or revision missing")
	}
	response = apiRequest(handler, "POST", "/api/gpa-simulation/export", `{"filename":"plan","revision":0,"data":{"courses":[]}}`, cookie)
	if response.Code != 409 {
		t.Fatal("blind overwrite succeeded")
	}
	apiRequest(handler, "POST", "/api/gpa-simulation/export", `{"filename":"other","revision":0,"data":{"courses":[]}}`, cookie)
	response = apiRequest(handler, "POST", "/api/gpa-simulation/rename", `{"filename":"plan","new_filename":"other","revision":1}`, cookie)
	if response.Code != 409 {
		t.Fatal("rename overwrote another document")
	}
	response = apiRequest(handler, "GET", "/api/gpa-simulation/file/plan.json", "", cookie)
	if response.Code != 200 {
		t.Fatal("failed rename deleted source")
	}
	response = apiRequest(handler, "POST", "/api/gpa-simulation/rename", `{"filename":"plan","new_filename":"new","revision":1}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/gpa-simulation/files", "", cookie)
	var files []map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &files); err != nil || len(files) != 2 {
		t.Fatal("file list includes deleted source")
	}
	request := httptest.NewRequest("DELETE", "http://localhost/api/gpa-simulation/file/new.json", nil)
	request.AddCookie(cookie)
	request.Header.Set("If-Match", "1")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "POST", "/api/gpa-simulation/export", `{"filename":"new","revision":0,"data":{"courses":[]}}`, cookie)
	if response.Code != 409 {
		t.Fatal("deleted plan resurrected")
	}
}

func TestGPAConcurrentEditsPreserveWinningCourses(t *testing.T) {
	_, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	const endpoint = "/api/gpa-simulation/export"
	created := apiRequest(handler, "POST", endpoint, `{"filename":"shared","revision":0,"data":{"courses":[]}}`, cookie)
	if created.Code != 200 {
		t.Fatal(created.Body.String())
	}
	type attempt struct {
		course string
		status int
	}
	start := make(chan struct{})
	results := make(chan attempt, 2)
	for _, course := range []string{"first draft", "second draft"} {
		go func(course string) {
			<-start
			body := `{"filename":"shared","revision":1,"data":{"courses":[{"name":"` + course + `"}]}}`
			response := apiRequest(handler, "POST", endpoint, body, cookie)
			results <- attempt{course, response.Code}
		}(course)
	}
	close(start)
	first, second := <-results, <-results
	if first.status == 409 {
		first, second = second, first
	}
	if first.status != 200 || second.status != 409 {
		t.Fatalf("unexpected concurrent responses: %+v %+v", first, second)
	}
	current := apiRequest(handler, "GET", "/api/gpa-simulation/file/shared.json", "", cookie)
	if current.Code != 200 || current.Header().Get("ETag") != `"2"` || !strings.Contains(current.Body.String(), first.course) || strings.Contains(current.Body.String(), second.course) {
		t.Fatal("stored plan does not match the successful edit", current.Body.String())
	}
}
