package application

import (
	"net/http"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
)

func (api *API) registerExams(router *http.ServeMux) {
	router.HandleFunc("GET /api/exams/terms", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		payload, err := academic.FetchExamTerms(request.Context(), runtime)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	for _, path := range []string{"/api/exams", "/api/exams/export-ics"} {
		router.HandleFunc("GET "+path, api.exams)
	}
}

func (api *API) exams(response http.ResponseWriter, request *http.Request) {
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	term := request.URL.Query().Get("term_code")
	if term != "" && !academic.ValidCourseCode(term) {
		writeJSON(response, 422, map[string]string{"detail": "学期代码无效"})
		return
	}
	payload, err := academic.FetchExams(request.Context(), runtime, term)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if request.URL.Path == "/api/exams" {
		writeJSON(response, 200, payload)
		return
	}
	exams := payload["exams"].([]map[string]any)
	if len(exams) == 0 {
		writeJSON(response, 404, map[string]string{"detail": "该学期暂无考试安排"})
		return
	}
	calendar, count := academic.ExamsCalendar(exams, runtime.Client.Account(), academic.Text(payload["term_code"]), time.Now())
	if count == 0 {
		writeJSON(response, 422, map[string]string{"detail": "考试时间尚未明确，无法导出日历"})
		return
	}
	response.Header().Set("Content-Type", "text/calendar; charset=utf-8")
	response.Header().Set("Content-Disposition", `attachment; filename="exams.ics"`)
	response.Header().Set("Cache-Control", "no-store")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.Header().Set("X-Skipped-Exams", strconv.Itoa(len(exams)-count))
	response.WriteHeader(200)
	response.Write([]byte(calendar))
}
