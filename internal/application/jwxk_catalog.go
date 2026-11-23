package application

import (
	"context"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func (api *API) jwxkCatalogSearch(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	input := academic.JWXKCatalogSearch{Page: 1, Size: 20, Scope: "ALL", OnlineMode: "all"}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "目录查询参数无效"})
		return
	}
	key := jwxkQueryKey(input)
	document, snapshot, err := api.jwxkQuerySnapshot(request.Context(), session, key)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if input.LocalOnly {
		archiveDocument, archive, err := api.readJWXKArchive(request.Context(), session, input.Batch)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		if archive != nil && jwxkArchiveSupports(input) {
			result := academic.QueryJWXKArchive(jwxkArchiveRows(archive["courses"]), input)
			result["scope_options"] = archive["scope_options"]
			result["catalog_complete"] = archive["catalog_complete"] == true
			result["sync_status"] = archive["sync_status"]
			result["snapshot_revision"], result["snapshot_updated_at"] = archiveDocument.Revision, archiveDocument.Updated
			writeJSON(response, 200, result)
			return
		}
		if snapshot != nil {
			snapshot["cache_hit"], snapshot["data_source"] = true, "local"
			snapshot["snapshot_revision"], snapshot["snapshot_updated_at"] = document.Revision, document.Updated
			writeJSON(response, 200, snapshot)
			return
		}
		writeJSON(response, 200, map[string]any{"total": 0, "groups": []any{}, "scope": input.Scope, "scope_options": []any{}, "cache_hit": false, "data_source": "local", "sync_status": "", "catalog_complete": false})
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
	archiveDocument, archive, err := api.readJWXKArchive(ctx, session, input.Batch)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	payload, err := academic.SearchJWXKCatalog(ctx, runtime, mode, input)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if err := api.saveJWXKQuerySnapshot(ctx, session, key, payload, document.Revision); err != nil {
		api.documentError(response, err)
		return
	}
	if err := api.mergeJWXKArchive(ctx, session, input.Batch, archiveDocument, archive, payload); err != nil {
		api.documentError(response, err)
		return
	}
	_, currentArchive, err := api.readJWXKArchive(ctx, session, input.Batch)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if currentArchive != nil {
		if jwxkArchiveSupports(input) && (currentArchive["catalog_complete"] == true || len(jwxkArchiveRows(payload["groups"])) == 0) {
			archived := academic.QueryJWXKArchive(jwxkArchiveRows(currentArchive["courses"]), input)
			payload["groups"], payload["total"] = archived["groups"], archived["total"]
		}
		payload["catalog_complete"], payload["sync_status"] = currentArchive["catalog_complete"] == true, currentArchive["sync_status"]
	}
	if job, err := api.scheduleJWXKScan(ctx, session, input.Batch, mode); err == nil && job != "" {
		payload["sync_status"], payload["sync_job_id"] = "queued", job
	}
	writeJSON(response, 200, payload)
}
