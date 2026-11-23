package application

import (
	"context"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func (api *API) jwxkSelectionSchedule(response http.ResponseWriter, request *http.Request) {
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
	payload, err := academic.FetchJWXKSelectionSchedule(ctx, runtime, mode, input.Batch)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, payload)
}
