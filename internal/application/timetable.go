package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func (api *API) officialRoomAvailability(response http.ResponseWriter, request *http.Request) {
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var input academic.OfficialRoomQuery
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "请选择有效日期和第1至12节的起止节次"})
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), 8*time.Second)
	defer cancel()
	items, err := academic.FetchOfficialAvailableRooms(ctx, runtime, input)
	if request.Context().Err() != nil || !api.allowRuntimeResponse(response, request, runtime) {
		return
	}
	if err == nil {
		writeJSON(response, 200, map[string]any{"source": "official", "items": items})
		return
	}
	if errors.Is(err, context.DeadlineExceeded) {
		writeJSON(response, 200, map[string]string{"source": "schedule"})
		return
	}
	var failure *campus.Error
	if errors.As(err, &failure) {
		switch failure.Code {
		case "UPSTREAM_UNAVAILABLE", "UPSTREAM_HTTP_ERROR", "UPSTREAM_RESPONSE_INVALID", "TIMETABLE_REJECTED":
			writeJSON(response, 200, map[string]string{"source": "schedule"})
			return
		}
	}
	api.resourceError(response, err)
}

func (api *API) registerTimetable(router *http.ServeMux) {
	router.HandleFunc("POST /api/timetable/rooms/official-availability", api.officialRoomAvailability)
	router.HandleFunc("POST /api/timetable/rooms/availability", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		input := academic.RoomAvailabilityQuery{Limit: 1}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "空闲教室查询条件无效"})
			return
		}
		payload, err := academic.ScanAvailableRooms(request.Context(), runtime, input)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("POST /api/timetable/targets/filter-options", api.timetableFilters)
	router.HandleFunc("POST /api/timetable/targets/search", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		input := academic.TimetableTargetQuery{Page: 1, PageSize: 20}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "对象查询条件无效"})
			return
		}
		payload, err := academic.SearchTimetableTargets(request.Context(), runtime, input)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("POST /api/timetable/schedule", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		input := academic.TimetableSchedule{TimetableContext: academic.TimetableContext{Mode: "personal"}}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "课表查询条件无效"})
			return
		}
		payload, err := academic.FetchTimetableSchedule(request.Context(), runtime, input)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("GET /api/timetable/terms", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		if entry, err := api.database.ReadCache(request.Context(), session, "timetable-index", "default"); err == nil && !entry.Stale(10*time.Minute) {
			var payload map[string]any
			if json.Unmarshal(entry.Payload, &payload) == nil {
				writeJSON(response, 200, payload)
				return
			}
		}
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		payload, err := academic.FetchTimetableTerms(request.Context(), runtime)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("POST /api/timetable/context", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		input := academic.TimetableContext{Mode: "personal"}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.Valid() {
			writeJSON(response, 422, map[string]string{"detail": "课表查询条件无效"})
			return
		}
		payload, err := academic.FetchTimetableContext(request.Context(), runtime, input)
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
}
