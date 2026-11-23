package application

import (
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

var outlineAttachmentPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,160}$`)

func (api *API) outlineRuntime(response http.ResponseWriter, request *http.Request) *Runtime {
	session, ok := api.requireSession(response, request)
	if !ok {
		return nil
	}
	runtime := api.accounts.Runtime(session)
	if runtime == nil {
		writeJSON(response, 401, map[string]string{"detail": "请先恢复学校登录"})
		return nil
	}
	if active, _ := runtime.Client.Status(); !active {
		writeJSON(response, 401, map[string]string{"detail": "请先恢复学校登录"})
		return nil
	}
	return runtime
}

func (api *API) allowRuntimeResponse(response http.ResponseWriter, request *http.Request, runtime *Runtime) bool {
	session, err := api.session(request)
	if err != nil || api.accounts.Runtime(session) != runtime {
		api.resourceError(response, store.ErrAccess)
		return false
	}
	return true
}

func (api *API) registerOutlineQuery(router *http.ServeMux) {
	router.HandleFunc("POST /api/course-outlines/detail/sections", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		var input struct {
			Code  string `json:"course_code"`
			Group string `json:"group"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		input.Code = strings.TrimSpace(input.Code)
		if !academic.ValidCourseCode(input.Code) || !academic.ValidOutlineGroup(input.Group) {
			writeJSON(response, 422, map[string]string{"detail": "课程代码或分组无效"})
			return
		}
		payload, err := academic.OutlineSections(request.Context(), runtime, input.Code, input.Group)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("POST /api/course-outlines/attachments/download", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		input := struct {
			Token    string `json:"token"`
			Filename string `json:"filename"`
		}{Filename: "课程大纲附件"}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !outlineAttachmentPattern.MatchString(input.Token) || input.Token == "." || input.Token == ".." || len([]rune(input.Filename)) < 1 || len([]rune(input.Filename)) > 120 || strings.ContainsAny(input.Filename, "\r\n\x00") {
			writeJSON(response, 422, map[string]string{"detail": "附件标识或名称无效"})
			return
		}
		payload, err := runtime.Request(request.Context(), http.MethodGet, "https://jwxt.neu.edu.cn/jwapp/sys/emapcomponent/file/getAttachmentFile/"+input.Token, nil)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		if !api.allowRuntimeResponse(response, request, runtime) {
			return
		}
		response.Header().Set("Content-Type", "application/octet-stream")
		response.Header().Set("Content-Disposition", "attachment; filename*=UTF-8''"+strings.ReplaceAll(url.QueryEscape(input.Filename), "+", "%20"))
		response.Header().Set("Cache-Control", "no-store")
		response.Header().Set("X-Content-Type-Options", "nosniff")
		response.WriteHeader(200)
		response.Write(payload)
	})
	router.HandleFunc("GET /api/course-outlines/search-schema", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		payload, err := academic.OutlineSearchSchema(request.Context(), runtime)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("POST /api/course-outlines/search", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		input := academic.OutlineSearch{Page: 1, PageSize: 20}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "大纲查询条件无效"})
			return
		}
		payload, err := academic.SearchOutlines(request.Context(), runtime, input)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("POST /api/course-outlines/detail/overview", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		var input struct {
			Code string `json:"course_code"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		input.Code = strings.TrimSpace(input.Code)
		if !academic.ValidCourseCode(input.Code) {
			writeJSON(response, 422, map[string]string{"detail": "课程代码无效"})
			return
		}
		payload, err := academic.OutlineOverview(request.Context(), runtime, input.Code)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
}
