package application

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"strings"
	"testing"
)

type exportFixtureClient struct{ testCampusClient }

type revokedExportClient struct {
	exportFixtureClient
	revoke func() error
}

func (client *revokedExportClient) Request(ctx context.Context, method, target string, form url.Values) ([]byte, error) {
	data, err := client.exportFixtureClient.Request(ctx, method, target, form)
	if err != nil {
		return nil, err
	}
	return data, client.revoke()
}

func TestReportExportWithRevokedSessionSendsNoAttachment(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	if err := api.database.AddAccount(ctx, "20250001"); err != nil {
		t.Fatal(err)
	}
	client := &revokedExportClient{}
	client.account = "20250001"
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, client.account)
	client.revoke = func() error { return api.database.RevokeSession(ctx, cookie.Value) }
	response := apiRequest(handler, "GET", "/api/academic-report/export", "", cookie)
	if response.Code == 200 || response.Header().Get("Content-Disposition") != "" || bytes.HasPrefix(response.Body.Bytes(), []byte("PK")) {
		t.Fatal("revoked session received attachment", response.Code)
	}
}

func (client *exportFixtureClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	if method != "POST" || !strings.HasSuffix(target, "/queryXyzhbx.do") || form.Get("fromPage") != "grxyjcbg" {
		return nil, errors.New("unexpected synthetic request")
	}
	return []byte(`{"code":"0","datas":{"queryXyzhbx":{"officialOnly":"保留原值","fanbx":{"name":"合成方案","children":[{"name":"必修","checkCourseVOS":[{"courseName":"=样例,课程","courseId":"IN","studentId":"20250001","credit":2,"passed":true}]}]},"fawbx":[{"courseName":"方案外","courseId":"OUT"}]}}}`), nil
}

func TestReportExportHTTPArchive(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	if err := api.database.AddAccount(context.Background(), "20250001"); err != nil {
		t.Fatal(err)
	}
	api.accounts.factory = func(account string) (CampusClient, error) {
		return &exportFixtureClient{testCampusClient{account: account}}, nil
	}
	const endpoint = "/api/academic-report/export"
	if anonymous := apiRequest(handler, "GET", endpoint, "", nil); anonymous.Code != 401 {
		t.Fatal(anonymous.Code)
	}
	response := apiRequest(handler, "GET", endpoint, "", loginCookie(t, handler, "20250001"))
	if response.Code != 200 || response.Header().Get("Content-Type") != "application/zip" || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(response.Code, response.Body.String())
	}
	archive, err := zip.NewReader(bytes.NewReader(response.Body.Bytes()), int64(response.Body.Len()))
	if err != nil {
		t.Fatal(err)
	}
	expected := map[string]string{"academic_report_basic.csv": "合成方案", "academic_report_categories.csv": "必修", "academic_report_courses.csv": "'=样例,课程", "academic_report_outside.csv": "OUT", "academic_report_full.json": "保留原值"}
	for _, file := range archive.File {
		want, ok := expected[file.Name]
		if !ok {
			t.Fatal("unexpected entry", file.Name)
		}
		delete(expected, file.Name)
		reader, err := file.Open()
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(reader)
		reader.Close()
		if err != nil {
			t.Fatal(err)
		}
		if strings.HasSuffix(file.Name, ".json") {
			var raw map[string]any
			if json.Unmarshal(data, &raw) != nil || raw["officialOnly"] != want {
				t.Fatal(file.Name)
			}
			continue
		}
		if !bytes.HasPrefix(data, []byte{0xef, 0xbb, 0xbf}) {
			t.Fatal("missing BOM", file.Name)
		}
		rows, err := csv.NewReader(bytes.NewReader(data[3:])).ReadAll()
		if err != nil || len(rows) != 2 {
			t.Fatal(file.Name, rows, err)
		}
		found := false
		for _, field := range rows[1] {
			if field == want {
				found = true
			}
		}
		if !found {
			t.Fatal(file.Name, rows)
		}
	}
	if len(expected) != 0 {
		t.Fatal("missing entries", expected)
	}
}
