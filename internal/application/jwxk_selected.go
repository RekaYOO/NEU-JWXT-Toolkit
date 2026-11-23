package application

import (
	"context"
	"net/http"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func (api *API) jwxkSelected(response http.ResponseWriter, request *http.Request) {
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
	if !(academic.JWXKSearch{Batch: input.Batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "选课轮次无效"})
		return
	}
	includeMarket := true
	if values, exists := request.URL.Query()["include_market"]; exists {
		var err error
		if len(values) == 1 {
			includeMarket, err = strconv.ParseBool(values[0])
		}
		if len(values) != 1 || err != nil {
			writeJSON(response, 422, map[string]string{"detail": "行情刷新参数无效"})
			return
		}
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
	result, err := academic.FetchJWXKSelected(ctx, runtime, mode, input.Batch)
	if err == nil {
		err = api.syncJWXKSelectedSnapshot(ctx, session, input.Batch, result)
	}
	release()
	if err != nil {
		api.resourceError(response, err)
		return
	}
	live := map[string]map[string]any{}
	seen := map[string]bool{}
	marketComplete := true
	if includeMarket {
	refresh:
		for _, feed := range []string{"selected", "volunteered", "withdrawal"} {
			for _, course := range result[feed].([]map[string]any) {
				code, scope := academic.Text(course["course_code"]), academic.Text(course["teaching_class_type"])
				search := academic.JWXKSearch{Batch: input.Batch, Scope: scope, Page: 1, Size: 50, Keyword: code}
				if code == "" || scope == "ALLKC" || !search.Valid() {
					marketComplete = false
					continue
				}
				key := strings.ToUpper(code) + ":" + scope
				if seen[key] {
					continue
				}
				seen[key] = true
				release, err := api.resources.acquireAccount(ctx, session)
				if err != nil {
					marketComplete = false
					break refresh
				}
				page, err := academic.SearchJWXKCourses(ctx, runtime, mode, search)
				release()
				if err != nil {
					marketComplete = false
					break refresh
				}
				for _, current := range page["courses"].([]map[string]any) {
					if academic.Text(current["course_code"]) == code {
						live[academic.Text(current["class_id"])] = current
					}
				}
			}
		}
	}
	if ctx.Err() != nil {
		api.resourceError(response, ctx.Err())
		return
	}
	if includeMarket {
		for _, feed := range []string{"selected", "volunteered", "withdrawal"} {
			for _, course := range result[feed].([]map[string]any) {
				if live[academic.Text(course["class_id"])] == nil {
					marketComplete = false
				}
			}
		}
	}
	academic.EnrichJWXKSelected(result, live, input.Batch)
	result["market_requested"], result["market_complete"] = includeMarket, includeMarket && marketComplete
	writeJSON(response, 200, result)
}
