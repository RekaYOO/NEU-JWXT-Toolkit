package campus

import (
	"context"
	"errors"
	"net/http"
	"testing"
)

func TestAcademicDocumentRenderNeverReplays(t *testing.T) {
	for _, scenario := range []string{"pdf", "lost", "repeat-redirect", "external-redirect", "webvpn"} {
		t.Run(scenario, func(t *testing.T) {
			client, _ := NewClient("student")
			mode := "direct"
			if scenario == "webvpn" {
				mode = "webvpn"
			}
			client.setStatus(true, mode)
			calls := 0
			client.http.Transport = transportFunc(func(request *http.Request) (*http.Response, error) {
				calls++
				if request.Method != "GET" || request.URL.Query().Get("wid") != "document" {
					t.Fatal("incorrect render", request.URL)
				}
				if scenario == "webvpn" && request.URL.Hostname() != vpnHost {
					t.Fatal("render escaped selected route")
				}
				if scenario == "lost" {
					return nil, errors.New("synthetic connection lost")
				}
				if scenario == "repeat-redirect" || scenario == "external-redirect" {
					response := mockResponse(request, 302, "")
					target := request.URL.String()
					if scenario == "external-redirect" {
						target = "https://external.invalid/report"
					}
					response.Header.Set("Location", target)
					return response, nil
				}
				response := mockResponse(request, 200, "%PDF-synthetic")
				response.Header.Set("Content-Type", "application/pdf")
				return response, nil
			})
			file, err := client.GenerateAcademicDocument(context.Background(), "document")
			if calls != 1 {
				t.Fatal("render replayed", calls)
			}
			if scenario == "pdf" || scenario == "webvpn" {
				if err != nil || file.ContentType != "application/pdf" {
					t.Fatal(file, err)
				}
			} else if err == nil {
				t.Fatal("unsafe response accepted")
			}
		})
	}
}
