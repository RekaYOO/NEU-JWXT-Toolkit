package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) scheduleConflicts(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Term       string           `json:"term_code"`
		Week       *int             `json:"week"`
		Candidates []map[string]any `json:"candidates"`
		IgnoreSame bool             `json:"ignore_same_course"`
		Resolve    bool             `json:"resolve_personal_timetable"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if !(academic.TimetableContext{Mode: "personal", Term: input.Term}).Valid() || len(input.Candidates) < 1 || len(input.Candidates) > 500 || input.Week != nil && (*input.Week < 1 || *input.Week > 30) {
		writeJSON(response, 422, map[string]string{"detail": "冲突检测学期、周次或候选数量无效"})
		return
	}
	candidates := []academic.SelectionMeeting{}
	for _, row := range input.Candidates {
		if !academic.ValidScheduleCandidate(row) {
			writeJSON(response, 422, map[string]string{"detail": "候选课时字段或时间范围无效"})
			return
		}
		candidates = append(candidates, academic.NormalizeScheduleMeeting(row, input.Term, "candidate", input.Week))
	}
	baseline := []academic.SelectionMeeting{}
	available, stale := false, true
	var revision any
	consume := func(payload []byte, week *int) bool {
		var result struct {
			Term    string           `json:"term_code"`
			Courses []map[string]any `json:"courses"`
		}
		if json.Unmarshal(payload, &result) != nil || result.Term != input.Term || result.Courses == nil {
			return false
		}
		for _, row := range result.Courses {
			if row != nil {
				baseline = append(baseline, academic.NormalizeScheduleMeeting(row, input.Term, "personal_timetable", week))
			}
		}
		return true
	}
	entry, err := api.database.ReadCache(request.Context(), session, "personal-timetable", "term:"+input.Term)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.resourceError(response, err)
		return
	}
	if err == nil && entry.SchemaVersion == 3 && consume(entry.Payload, input.Week) {
		available, stale, revision = true, entry.Stale(timetableTTL), entry.Revision
	}
	if input.Resolve {
		baseline = []academic.SelectionMeeting{}
		available, stale, revision = false, true, nil
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		var live map[string]any
		err := api.resources.documentWorkStep(request.Context(), session, func() error {
			var err error
			live, err = academic.FetchTimetableSchedule(request.Context(), runtime, academic.TimetableSchedule{TimetableContext: academic.TimetableContext{Mode: "personal", Term: input.Term, Campus: "all"}})
			return err
		})
		if err != nil {
			var failure *campus.Error
			if errors.Is(err, store.ErrAccess) || errors.Is(err, context.Canceled) || errors.As(err, &failure) && (strings.Contains(failure.Code, "AUTH") || strings.Contains(failure.Code, "LOGIN")) {
				api.resourceError(response, err)
				return
			}
		} else {
			payload, _ := json.Marshal(live)
			available = consume(payload, nil)
			stale = !available
		}
	}
	writeJSON(response, 200, map[string]any{"term_code": input.Term, "week": input.Week, "baseline_count": len(baseline), "baseline_available": available, "baseline_revision": revision, "baseline_stale": stale || !available, "candidate_count": len(candidates), "results": academic.CheckScheduleConflicts(baseline, candidates, available, stale, input.IgnoreSame)})
}
