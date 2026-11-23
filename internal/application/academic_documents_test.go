package application

import (
	"bytes"
	"context"
	"errors"
	"net/url"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"golang.org/x/text/encoding/simplifiedchinese"
)

type documentTestClient struct {
	testCampusClient
	preflight string
	file      campus.AcademicDocumentFile
	renders   int
	lost      bool
	calls     []string
}

func (client *documentTestClient) Request(_ context.Context, method, target string, form url.Values) ([]byte, error) {
	client.calls = append(client.calls, target)
	switch {
	case strings.Contains(target, "*default/index.do"):
		return []byte(`<html></html>`), nil
	case strings.HasSuffix(target, "queryAllZmdy.do"):
		return []byte(`{"success":true,"datas":[{"CDMC":"成绩单","REPORTS":[{"WID":"document","CDMC":"中文成绩单","XNXQWD_DBDX":"50"}]},{"CDMC":"学籍证明","REPORTS":[{"WID":"enrollment","CDMC":"学籍证明"}]}]}`), nil
	case strings.HasSuffix(target, "printBefore.do"):
		if method != "POST" || form.Get("wid") != "document" {
			return nil, errors.New("wrong preflight")
		}
		if client.preflight != "" {
			return []byte(client.preflight), nil
		}
		return []byte(`{"success":true,"hasQx":true,"fwlx":"1"}`), nil
	}
	return nil, errors.New("unexpected request")
}

func (client *documentTestClient) GenerateAcademicDocument(context.Context, string) (campus.AcademicDocumentFile, error) {
	client.renders++
	if client.lost {
		return campus.AcademicDocumentFile{}, errors.New("synthetic render lost")
	}
	return client.file, nil
}

func TestAcademicDocumentDiscoveryAndSingleGeneration(t *testing.T) {
	for _, scenario := range []string{"pdf", "html", "denied", "application-only", "unknown-id", "lost", "json"} {
		t.Run(scenario, func(t *testing.T) {
			api, handler := testApplication(t, "local-single")
			client := &documentTestClient{testCampusClient: testCampusClient{account: "student"}, file: campus.AcademicDocumentFile{Body: []byte("%PDF-synthetic"), ContentType: "application/octet-stream"}}
			if scenario == "html" {
				client.file = campus.AcademicDocumentFile{Body: []byte(`<html><HEAD><title>report</title></HEAD><body>报告</body></html>`), ContentType: "text/html; charset=utf-8"}
			}
			if scenario == "denied" {
				client.preflight = `{"success":true,"hasQx":false}`
			}
			if scenario == "application-only" {
				client.preflight = `{"success":true,"hasQx":true,"fwlx":"2"}`
			}
			if scenario == "json" {
				client.file = campus.AcademicDocumentFile{Body: []byte(`{"success":false}`), ContentType: "application/json"}
			}
			client.lost = scenario == "lost"
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			cookie := loginCookie(t, handler, "student")
			list := apiRequest(handler, "GET", "/api/export/academic-documents", "", cookie)
			if list.Code != 200 || !strings.Contains(list.Body.String(), `"semester_limit":50`) || !strings.Contains(list.Body.String(), `"groups"`) || client.renders != 0 {
				t.Fatal(list.Body.String(), client.renders)
			}
			body := `{"document_id":"document"}`
			if scenario == "unknown-id" {
				body = `{"document_id":"unknown"}`
			}
			response := apiRequest(handler, "POST", "/api/export/academic-documents/generate", body, cookie)
			if !strings.Contains(response.Header().Get("Cache-Control"), "no-store") {
				t.Fatal("document may be cached")
			}
			if scenario == "pdf" || scenario == "html" {
				if response.Code != 200 || client.renders != 1 || !strings.Contains(response.Header().Get("Content-Disposition"), "UTF-8''") {
					t.Fatal(response.Code, response.Body.String())
				}
				if scenario == "html" && !strings.Contains(response.Body.String(), `<base href="https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/">`) {
					t.Fatal("missing report resource base")
				}
			} else if response.Code == 200 {
				t.Fatal("invalid generation accepted", scenario)
			}
			if scenario == "denied" || scenario == "application-only" || scenario == "unknown-id" {
				if client.renders != 0 {
					t.Fatal("render bypassed preflight")
				}
			}
			if client.renders > 1 {
				t.Fatal("render retried")
			}
			api.resources.mutex.Lock()
			defer api.resources.mutex.Unlock()
			if len(api.resources.jobs) != 0 {
				t.Fatal("documents registered as cache")
			}
		})
	}
}

func TestAcademicDocumentRejectsLoginHTMLAndUnsafeNames(t *testing.T) {
	if _, _, err := prepareAcademicDocument(campus.AcademicDocumentFile{ContentType: "text/html", Body: []byte(`<html><form id="loginForm"><input name="password"></form></html>`)}); err == nil {
		t.Fatal("login page treated as proof")
	}
	if filename := documentFilename("../姓名\r\n/证明", "pdf"); strings.ContainsAny(filename, "/\r\n") || !strings.HasSuffix(filename, ".pdf") {
		t.Fatal(filename)
	}
}

func TestAcademicDocumentConvertsChineseEncodingAndBoundsContent(t *testing.T) {
	encoded, err := simplifiedchinese.GBK.NewEncoder().Bytes([]byte(`<html><head><meta charset="gbk"></head><body>中文证明</body></html>`))
	if err != nil {
		t.Fatal(err)
	}
	body, format, err := prepareAcademicDocument(campus.AcademicDocumentFile{ContentType: "text/html; charset=gbk", Body: encoded})
	if err != nil || format != "html" || !bytes.Contains(body, []byte("中文证明")) || !bytes.Contains(body, []byte(`<head><meta charset="utf-8">`)) {
		t.Fatal(string(body), format, err)
	}
	if _, _, err := prepareAcademicDocument(campus.AcademicDocumentFile{ContentType: "application/pdf", Body: bytes.Repeat([]byte("x"), campus.MaxAcademicDocumentBytes+1)}); err == nil {
		t.Fatal("oversized file accepted")
	}
}
