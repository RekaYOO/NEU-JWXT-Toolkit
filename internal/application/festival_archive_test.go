package application

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"testing"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type festivalArchiveClient struct {
	festivalTestClient
	count               int
	image               []byte
	extra               bool
	downloads           int
	failed, authFailure bool
}

func (client *festivalArchiveClient) RequestService(ctx context.Context, input campus.ServiceRequest) ([]byte, error) {
	if strings.HasPrefix(input.Path, "/popscience/comp/ucenter/") {
		client.calls.Add(1)
		if client.count > 0 {
			var rows strings.Builder
			for index := 1; index <= client.count; index++ {
				fmt.Fprintf(&rows, `<div class="list_item"><a href="../../front/comp/info?id=%d">activity</a><img src="/static/uploads/res/popsciencecert/%d.png"></div>`, index, index)
			}
			return []byte(rows.String()), nil
		}
		return []byte(`<div class="list_item"><a href="../../front/comp/info?id=1">one</a><img src="/static/uploads/res/popsciencecert/one.png"></div><div class="list_item"><a href="../../front/comp/info?id=2">two</a><img src="/static/uploads/res/popsciencecert/two.png"></div>`), nil
	}
	return client.festivalTestClient.RequestService(ctx, input)
}

func (client *festivalArchiveClient) DownloadService(_ context.Context, mode, path string, normalize func(string) string) (campus.ServiceFile, error) {
	client.downloads++
	if client.image != nil {
		body := client.image
		if client.extra && client.downloads == 1 {
			body = body[:len(body)-8]
		}
		if client.extra && client.downloads == 11 {
			body = body[:9]
		}
		return campus.ServiceFile{Body: body, ContentType: "image/png", URL: path}, nil
	}
	if mode != "webvpn" || normalize(path) == "" {
		return campus.ServiceFile{}, errors.New("invalid frozen route or address")
	}
	if strings.Contains(path, "two.png") && client.authFailure {
		return campus.ServiceFile{}, &campus.Error{Code: "SERVICE_AUTH_REQUIRED", Message: "synthetic private detail"}
	}
	if strings.Contains(path, "two.png") && client.failed {
		return campus.ServiceFile{Body: []byte(`<html>login</html>`), ContentType: "image/png", URL: path}, nil
	}
	return campus.ServiceFile{Body: []byte("\x89PNG\r\n\x1a\nsynthetic"), ContentType: "image/png", URL: path}, nil
}

func TestFestivalArchiveLimits(t *testing.T) {
	for _, scenario := range []struct {
		name                           string
		count, size, downloads, status int
		extra                          bool
	}{{"200 allowed", 200, 16, 200, 200, false}, {"201 refused", 201, 16, 0, 413, false}, {"100 MiB allowed", 10, 10 << 20, 10, 200, false}, {"over 100 MiB refused", 11, 10 << 20, 11, 413, true}} {
		t.Run(scenario.name, func(t *testing.T) {
			api, handler := testApplication(t, "local-single")
			client := &festivalArchiveClient{count: scenario.count, image: make([]byte, scenario.size), extra: scenario.extra}
			copy(client.image, "\x89PNG\r\n\x1a\n")
			client.account = "student"
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			result := apiRequest(handler, "POST", "/api/export/festival-activities/certificates/archive", `{"start_date":"2026-11-16","end_date":"2026-11-16"}`, loginCookie(t, handler, "student"))
			if result.Code != scenario.status || client.downloads != scenario.downloads {
				t.Fatal(result.Code, client.downloads)
			}
			if result.Code == 413 && (result.Header().Get("Content-Type") == "application/zip" || bytes.HasPrefix(result.Body.Bytes(), []byte("PK"))) {
				t.Fatal("partial ZIP returned")
			}
		})
	}
}

func TestFestivalArchiveUsesFreshScanWithUniqueNamesAndPartialFailures(t *testing.T) {
	for _, scenario := range []string{"success", "partial", "auth", "empty"} {
		t.Run(scenario, func(t *testing.T) {
			api, handler := testApplication(t, "local-single")
			client := &festivalArchiveClient{festivalTestClient: festivalTestClient{testCampusClient: testCampusClient{account: "student"}, mode: "webvpn"}, failed: scenario == "partial", authFailure: scenario == "auth"}
			api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
			cookie := loginCookie(t, handler, "student")
			session, _ := api.database.Session(context.Background(), cookie.Value)
			if _, err := api.database.SaveForSession(context.Background(), session, "settings", "cxcy", json.RawMessage(`{"network_mode":"webvpn"}`), 0, false); err != nil {
				t.Fatal(err)
			}
			if _, err := api.database.CommitCache(context.Background(), session, store.CacheEntry{Resource: "festival-activities", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"activities":[],"warnings":[]}`), Revision: "stale"}, "test", nil); err != nil {
				t.Fatal(err)
			}
			body := `{"start_date":"2026-11-16","end_date":"2026-11-16"}`
			if scenario == "empty" {
				body = `{"start_date":"2026-11-17","end_date":"2026-11-17"}`
			}
			response := apiRequest(handler, "POST", "/api/export/festival-activities/certificates/archive", body, cookie)
			if client.calls.Load() != 6 {
				t.Fatal("archive did not re-fetch all sections and details", client.calls.Load())
			}
			if scenario == "auth" {
				if response.Code != 401 || strings.Contains(response.Body.String(), "private detail") {
					t.Fatal(response.Code, response.Body.String())
				}
				return
			}
			if scenario == "empty" {
				if response.Code != 404 || client.downloads != 0 {
					t.Fatal(response.Code, client.downloads)
				}
				return
			}
			if response.Code != 200 || client.downloads != 2 || response.Header().Get("Cache-Control") != "no-store" {
				t.Fatal(response.Code, response.Body.String())
			}
			archive, err := zip.NewReader(bytes.NewReader(response.Body.Bytes()), int64(response.Body.Len()))
			if err != nil {
				t.Fatal(err)
			}
			if len(archive.File) != 2 {
				t.Fatal(archive.File)
			}
			if scenario == "partial" {
				if response.Header().Get("X-Certificate-Succeeded") != "1" || response.Header().Get("X-Certificate-Failed") != "1" || archive.File[1].Name != "下载说明.txt" {
					t.Fatal(response.Header(), archive.File)
				}
				reader, _ := archive.File[1].Open()
				note, _ := io.ReadAll(reader)
				reader.Close()
				if string(note) != "以下证书下载失败：\n测试活动：图片或最终地址校验失败" {
					t.Fatal("failure note must identify the activity and explain its download failure")
				}
			} else if archive.File[0].Name == archive.File[1].Name || !strings.HasSuffix(archive.File[1].Name, "_2.png") {
				t.Fatal("duplicate names", archive.File)
			}
			entry, _ := api.database.ReadCache(context.Background(), session, "festival-activities", "default")
			if entry.Revision != "stale" {
				t.Fatal("archive changed cached data")
			}
		})
	}
}

func TestFestivalArchiveDateAndImageValidation(t *testing.T) {
	_, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	for _, body := range []string{`{}`, `{"start_date":"2026-02-30","end_date":"2026-03-01"}`, `{"start_date":"2026-01-01","end_date":"2027-02-01"}`, `{"start_date":"2026-02-01","end_date":"2026-01-01"}`, `{"start_date":"2026-01-01","end_date":"2026-01-01","url":"https://evil.invalid"}`} {
		if response := apiRequest(handler, "POST", "/api/export/festival-activities/certificates/archive", body, cookie); response.Code != 422 {
			t.Fatal(response.Code, body)
		}
	}
	if _, err := festivalImageType(campus.ServiceFile{Body: []byte("\x89PNG\r\n\x1a\n"), ContentType: "text/html"}); err == nil {
		t.Fatal("mime mismatch accepted")
	}
}
