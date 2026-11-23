package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) officialWithdrawals(response http.ResponseWriter, request *http.Request) {
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	selected := request.URL.Query().Get("term_code")
	if selected != "" && !(academic.OfficialWithdrawalInput{Term: selected, WID: "check", Confirmed: true}).Valid() {
		writeJSON(response, 422, map[string]string{"detail": "退课学期无效"})
		return
	}
	term, name, err := academic.OfficialWithdrawalTerm(request.Context(), runtime)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if selected == "" {
		selected = term
	}
	if selected != term {
		name = ""
	}
	result := map[string]any{"term_code": selected, "term_name": name, "entry_available": false, "entry_status": "closed", "courses": []any{}, "penalty_rules": map[string]any{}}
	if selected != "" {
		courses, rules, err := academic.FetchOfficialWithdrawals(request.Context(), runtime, selected, time.Now())
		if err != nil {
			api.resourceError(response, err)
			return
		}
		result["courses"], result["penalty_rules"], result["entry_available"], result["entry_status"] = courses, rules, true, "open"
		if len(courses) == 0 {
			result["entry_status"] = "empty"
		}
	}
	writeJSON(response, 200, result)
}

func (api *API) officialDeselect(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.OfficialWithdrawalInput
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "请确认退课并提供有效学期和课程标识"})
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var result map[string]any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		result, err = api.applyOfficialWithdrawal(request.Context(), runtime, session, input)
		return err
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if result["success"] != true {
		writeJSON(response, 409, result)
		return
	}
	writeJSON(response, 200, result)
}

func (api *API) applyOfficialWithdrawal(ctx context.Context, runtime *Runtime, session store.Session, input academic.OfficialWithdrawalInput) (map[string]any, error) {
	encoded, _ := json.Marshal([]string{"official-withdrawal", input.Term, input.WID})
	digest := sha256.Sum256(encoded)
	key := hex.EncodeToString(digest[:])
	previous, err := api.database.SelectionAttempt(ctx, session, key)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return nil, err
	}
	exists := err == nil
	term, _, err := academic.OfficialWithdrawalTerm(ctx, runtime)
	if err != nil {
		return nil, err
	}
	if term != input.Term {
		return map[string]any{"success": false, "detail": "退课学期已变化，请刷新后重试"}, nil
	}
	courses, _, err := academic.FetchOfficialWithdrawals(ctx, runtime, input.Term, time.Now())
	if err != nil {
		return nil, err
	}
	var target map[string]any
	for _, course := range courses {
		if course["wid"] == input.WID {
			target = course
			break
		}
	}
	if exists && previous.State != "rejected" && previous.State != "not_sent" {
		if target != nil {
			return map[string]any{"success": false, "state": "unknown", "detail": "已有提交记录，官方列表尚未确认移除；未重复退课"}, nil
		}
		if err := api.database.FinishSelection(ctx, session, key, previous, "confirmed"); err != nil {
			return nil, err
		}
		api.invalidateOfficialWithdrawal(ctx, runtime, session, input.Term)
		return map[string]any{"success": true, "state": "confirmed", "message": "已核验课程从官方列表移除，未重复提交"}, nil
	}
	if target == nil {
		return map[string]any{"success": false, "detail": "课程已不在官方退课列表中，请刷新后重试"}, nil
	}
	if target["can_withdraw"] != true {
		return map[string]any{"success": false, "detail": target["unavailable_reason"]}, nil
	}
	attempt, err := api.database.BeginSelectionOperation(ctx, session, key, academic.Text(target["course_code"]), input.Term, "official-withdrawal", input.WID)
	if err != nil {
		return nil, err
	}
	state, message := academic.SendOfficialWithdrawal(ctx, runtime, input)
	persist, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	if err := api.database.FinishSelection(persist, session, key, attempt, state); err != nil {
		return nil, err
	}
	if state == "confirmed" {
		api.invalidateOfficialWithdrawal(persist, runtime, session, input.Term)
	}
	return map[string]any{"success": state == "confirmed", "state": state, "message": message, "detail": message}, nil
}

func (api *API) invalidateOfficialWithdrawal(ctx context.Context, runtime *Runtime, session store.Session, term string) {
	if api.database.InvalidateCache(ctx, session, "personal-timetable", "term:"+term) == nil {
		api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+term, "jwxt.official_deselect", true)
	}
}
