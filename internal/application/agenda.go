package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) agendaCalendars(ctx context.Context, session store.Session) (map[string]*semesterSpan, string) {
	calendars := map[string]*semesterSpan{}
	variants, _ := api.database.CacheVariants(ctx, session, "personal-timetable")
	for _, variant := range variants {
		entry, err := api.database.ReadCache(ctx, session, "personal-timetable", variant)
		if err != nil || entry.SchemaVersion != 3 || entry.Stale(timetableTTL) {
			continue
		}
		var payload struct {
			Term  string           `json:"term_code"`
			Weeks []map[string]any `json:"weeks"`
		}
		if json.Unmarshal(entry.Payload, &payload) != nil || variant != "term:"+payload.Term {
			continue
		}
		if span := semesterCalendar(payload.Weeks); span != nil {
			calendars[payload.Term] = span
		}
	}
	current := ""
	if entry, err := api.database.ReadCache(ctx, session, "timetable-index", "default"); err == nil && !entry.Stale(10*time.Minute) {
		var index map[string]any
		if json.Unmarshal(entry.Payload, &index) == nil {
			if terms := timetableAllowed(index); len(terms) > 0 {
				current = terms[0]
			}
		}
	}
	return calendars, current
}

func (api *API) reconcileAgendas(ctx context.Context, session store.Session, calendars map[string]*semesterSpan, current, today string) error {
	documents, err := api.database.ListDocuments(ctx, session, "agenda")
	if err != nil {
		return err
	}
	nowSpan := calendars[current]
	started := nowSpan != nil && nowSpan.Start <= today && today <= nowSpan.End
	for _, document := range documents {
		var record agendaRecord
		if json.Unmarshal(document.Payload, &record) != nil || record.Events == nil || record.Moves == nil {
			continue
		}
		before, _ := json.Marshal(record)
		if span := calendars[document.Key]; span != nil && (record.Semester == nil || record.Semester.Start == span.Start) {
			end := span.End
			if record.Semester != nil && record.Semester.End > end {
				end = record.Semester.End
			}
			record.Semester = &semesterSpan{Start: span.Start, End: end}
		}
		if started && record.Semester != nil && record.Semester.End < nowSpan.Start && record.Semester.End < today && !record.Retired {
			record.Events, record.Moves, record.Retired = []AgendaEvent{}, []AgendaMove{}, true
		}
		after, _ := json.Marshal(record)
		if string(before) != string(after) {
			if _, err := api.database.SaveForSession(ctx, session, "agenda", document.Key, after, document.Revision, false); err != nil && !errors.Is(err, store.ErrConflict) {
				return err
			}
		}
	}
	return nil
}

func (api *API) registerAgenda(router *http.ServeMux) {
	router.HandleFunc("GET /api/timetable/agenda", api.agenda)
	router.HandleFunc("PUT /api/timetable/agenda", api.agenda)
}

func (api *API) agenda(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	term := request.URL.Query().Get("term_code")
	if !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "学期代码无效"})
		return
	}
	calendars, current := api.agendaCalendars(request.Context(), session)
	today := agendaToday()
	if err := api.reconcileAgendas(request.Context(), session, calendars, current, today); err != nil {
		api.documentError(response, err)
		return
	}
	document, err := api.database.Read(request.Context(), session.Account, "agenda", term)
	record := agendaRecord{Events: []AgendaEvent{}, Moves: []AgendaMove{}}
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	if err == nil {
		if document.Deleted || json.Unmarshal(document.Payload, &record) != nil || record.Events == nil || record.Moves == nil {
			writeJSON(response, 409, map[string]string{"detail": "日程资料不可读取，未覆盖已有内容"})
			return
		}
	}
	span := record.Semester
	if span == nil {
		span = calendars[term]
	}
	ended := record.Retired || span != nil && span.End < today
	if request.Method == http.MethodPut {
		if api.outlineRuntime(response, request) == nil {
			return
		}
		var input AgendaDocument
		if !decodeJSON(response, request, &input) {
			return
		}
		if !input.normalize() {
			writeJSON(response, 422, map[string]string{"detail": "日程日期、时间或重复标识无效"})
			return
		}
		if input.Revision != document.Revision {
			api.documentError(response, store.ErrConflict)
			return
		}
		if ended {
			writeJSON(response, 410, map[string]string{"detail": "该学期已结束，日程不再开放编辑"})
			return
		}
		old := map[string]bool{}
		for _, move := range record.Moves {
			old[move.Source+":"+move.Target] = true
		}
		for _, move := range input.Moves {
			if !old[move.Source+":"+move.Target] && move.Source <= today {
				writeJSON(response, 422, map[string]string{"detail": "只能调入未来日期的课程"})
				return
			}
			if span != nil && (move.Source < span.Start || move.Source > span.End || move.Target < span.Start || move.Target > span.End) {
				writeJSON(response, 422, map[string]string{"detail": "调休必须在同一学期内"})
				return
			}
		}
		record.Events, record.Moves, record.Semester = input.Events, input.Moves, span
		encoded, _ := json.Marshal(record)
		document, err = api.database.SaveForSession(request.Context(), session, "agenda", term, encoded, input.Revision, false)
		if err != nil {
			api.documentError(response, err)
			return
		}
	}
	var end any
	if span != nil {
		end = span.End
	}
	writeJSON(response, 200, map[string]any{"revision": document.Revision, "events": record.Events, "moves": record.Moves, "semester_ended": ended, "semester_end": end})
}
