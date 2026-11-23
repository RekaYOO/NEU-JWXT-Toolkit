package application

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func (api *API) jwxkFilters(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Batch string `json:"batch_code"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !(academic.JWXKSearch{Batch: input.Batch, Scope: "FANKC", Page: 1, Size: 50}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "轮次参数无效"})
		return
	}
	_, archive, err := api.readJWXKArchive(request.Context(), session, input.Batch)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if archive != nil {
		courses := jwxkArchiveRows(archive["courses"])
		if len(courses) > 0 || archive["catalog_complete"] == true {
			menus := []map[string]string{}
			for _, menu := range jwxkArchiveRows(archive["scope_options"]) {
				menus = append(menus, map[string]string{"code": academic.Text(menu["code"]), "name": academic.Text(menu["name"])})
			}
			writeJSON(response, 200, academic.JWXKFilterOptions(courses, menus, "", ""))
			return
		}
	}
	documents, err := api.database.ListDocuments(request.Context(), session, jwxkQueryNamespace)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	courses := []map[string]any{}
	menus := []map[string]string{}
	seen := map[string]bool{}
	for _, document := range documents {
		if len(document.Key) != len(input.Batch)+65 || !strings.HasPrefix(document.Key, input.Batch+"-") {
			continue
		}
		var payload struct {
			Groups []struct {
				Classes []map[string]any `json:"classes"`
			} `json:"groups"`
			Options []map[string]string `json:"scope_options"`
		}
		if json.Unmarshal(document.Payload, &payload) != nil {
			continue
		}
		for _, group := range payload.Groups {
			courses = append(courses, group.Classes...)
		}
		for _, menu := range payload.Options {
			if code := menu["code"]; code != "" && !seen[code] {
				seen[code] = true
				menus = append(menus, menu)
			}
		}
	}
	if len(courses) > 0 {
		writeJSON(response, 200, academic.JWXKFilterOptions(courses, menus, "", ""))
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	mode, _, err := api.jwxkPreference(request.Context(), session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if mode == "follow" {
		_, mode = runtime.Client.Status()
	}
	ctx, cancel := context.WithCancel(request.Context())
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	release, err := api.resources.acquireAccount(ctx, session)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	defer release()
	payload, err := academic.FetchJWXKFilterOptions(ctx, runtime, mode, input.Batch)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}
