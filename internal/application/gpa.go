package application

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func gpaFilename(filename string) (string, bool) {
	filename = strings.TrimSpace(filename)
	if filename == "" || len(filename) > 240 || strings.ContainsAny(filename, "/\\\x00\r\n") || strings.HasPrefix(filename, ".") {
		return "", false
	}
	if !strings.HasSuffix(filename, ".json") {
		filename += ".json"
	}
	return filename, true
}

func (api *API) registerGPA(router *http.ServeMux) {
	router.HandleFunc("POST /api/gpa-simulation/export", api.gpaSave)
	router.HandleFunc("GET /api/gpa-simulation/files", api.gpaList)
	router.HandleFunc("GET /api/gpa-simulation/file/{filename}", api.gpaRead)
	router.HandleFunc("DELETE /api/gpa-simulation/file/{filename}", api.gpaDelete)
	router.HandleFunc("POST /api/gpa-simulation/rename", api.gpaRename)
}

func (api *API) gpaSave(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Filename string                     `json:"filename"`
		Data     map[string]json.RawMessage `json:"data"`
		Revision int64                      `json:"revision"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	filename, valid := gpaFilename(input.Filename)
	var courses []json.RawMessage
	if !valid || input.Data == nil || json.Unmarshal(input.Data["courses"], &courses) != nil || courses == nil || len(courses) > 20000 {
		writeJSON(response, 422, map[string]string{"detail": "文件名或方案课程结构无效"})
		return
	}
	delete(input.Data, "_document_revision")
	metadata, _ := json.Marshal(map[string]any{"exported_by": session.Account, "exported_at": time.Now().UTC().Format(time.RFC3339Nano), "version": "2.0"})
	input.Data["export_info"] = metadata
	payload, err := json.Marshal(input.Data)
	if err != nil {
		writeJSON(response, 422, map[string]string{"detail": "方案内容无效"})
		return
	}
	document, err := api.database.SaveForSession(request.Context(), session, "gpa", filename, payload, input.Revision, false)
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "filename": filename, "revision": document.Revision})
}

func (api *API) gpaList(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	documents, err := api.database.ListDocuments(request.Context(), session, "gpa")
	if err != nil {
		api.documentError(response, err)
		return
	}
	files := []map[string]any{}
	for _, document := range documents {
		var payload map[string]any
		if json.Unmarshal(document.Payload, &payload) != nil {
			continue
		}
		files = append(files, map[string]any{"filename": document.Key, "revision": document.Revision, "size": len(document.Payload), "modified_time": document.Updated, "stats": payload["stats"]})
	}
	writeJSON(response, 200, files)
}

func (api *API) gpaRead(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	filename, valid := gpaFilename(request.PathValue("filename"))
	if !valid {
		writeJSON(response, 422, map[string]string{"detail": "文件名无效"})
		return
	}
	document, err := api.database.Read(request.Context(), session.Account, "gpa", filename)
	if err == nil && document.Deleted {
		err = store.ErrMissing
	}
	if err != nil {
		api.documentError(response, err)
		return
	}
	var payload map[string]any
	if json.Unmarshal(document.Payload, &payload) != nil {
		writeJSON(response, 500, map[string]string{"detail": "保存的方案格式无效"})
		return
	}
	payload["_document_revision"] = document.Revision
	response.Header().Set("ETag", `"`+strconv.FormatInt(document.Revision, 10)+`"`)
	writeJSON(response, 200, payload)
}

func (api *API) gpaDelete(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	filename, valid := gpaFilename(request.PathValue("filename"))
	version := request.Header.Get("If-Match")
	if len(version) >= 2 && version[0] == '"' && version[len(version)-1] == '"' {
		version = version[1 : len(version)-1]
	}
	revision, err := strconv.ParseInt(version, 10, 64)
	if !valid || err != nil || revision < 1 {
		writeJSON(response, 428, map[string]string{"detail": "请刷新文件列表后再删除"})
		return
	}
	_, err = api.database.SaveForSession(request.Context(), session, "gpa", filename, json.RawMessage("null"), revision, true)
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "message": "文件已删除"})
}

func (api *API) gpaRename(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Filename    string `json:"filename"`
		NewFilename string `json:"new_filename"`
		Revision    int64  `json:"revision"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	source, validSource := gpaFilename(input.Filename)
	destination, validDestination := gpaFilename(input.NewFilename)
	if !validSource || !validDestination {
		writeJSON(response, 422, map[string]string{"detail": "文件名无效"})
		return
	}
	document, err := api.database.RenameDocument(request.Context(), session, "gpa", source, destination, input.Revision)
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "filename": destination, "revision": document.Revision})
}
