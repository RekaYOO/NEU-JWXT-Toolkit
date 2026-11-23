package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func jwxkSelectionKey(input academic.JWXKSelect) string {
	encoded, _ := json.Marshal([]string{"select", input.Batch, strings.ToUpper(input.Course)})
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func jwxkSelectionUnknown() academic.JWXKMutationResult {
	return academic.JWXKMutationResult{State: "unknown", Code: "unknown", Message: "该课程已有待核验的提交记录，请刷新官方结果；不会重复提交", Failure: "MUTATION_UNCERTAIN"}
}

func (api *API) selectJWXKCourse(ctx context.Context, runtime *Runtime, session store.Session, mode string, input academic.JWXKSelect) (academic.JWXKMutationResult, error) {
	operations, err := api.database.ListDocuments(ctx, session, "selection-attempts")
	if err != nil {
		return jwxkSelectionUnknown(), err
	}
	for _, operation := range operations {
		var removal store.SelectionAttempt
		if json.Unmarshal(operation.Payload, &removal) != nil {
			return jwxkSelectionUnknown(), store.ErrConflict
		}
		if removal.Batch == input.Batch && strings.EqualFold(removal.Course, input.Course) && (removal.State == "pending" || removal.State == "unknown") {
			result := jwxkSelectionUnknown()
			result.Message = "该课程的退课结果尚未确认，请先核验退课结果再重新选择"
			return result, nil
		}
	}
	key := jwxkSelectionKey(input)
	previous, err := api.database.SelectionAttempt(ctx, session, key)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return jwxkSelectionUnknown(), err
	}
	if err == nil && previous.State != "rejected" && previous.State != "not_sent" {
		verified, term, verifyErr := academic.VerifyJWXKSelection(ctx, runtime, mode, input)
		if verifyErr == nil && verified {
			if err := api.database.FinishSelection(ctx, session, key, previous, "confirmed"); err != nil {
				return jwxkSelectionUnknown(), err
			}
			if err := api.rearmConfirmedSelectionOperation(ctx, session, jwxkDeselectionKey(input.Batch, input.Class)); err != nil {
				return jwxkSelectionUnknown(), err
			}
			if term != "" {
				if err := api.database.InvalidateCache(ctx, session, "personal-timetable", "term:"+term); err != nil {
					return jwxkSelectionUnknown(), err
				}
				api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+term, "jwxk.select.verify", true)
			}
			return academic.JWXKMutationResult{State: "confirmed", Success: true, Code: "already_selected", Message: "已核验官方结果，该教学班已选中，未重复提交"}, nil
		}
		return jwxkSelectionUnknown(), nil
	}
	prepared, err := academic.PrepareJWXKSelection(ctx, runtime, mode, input)
	if err != nil {
		return jwxkSelectionUnknown(), err
	}
	attempt, err := api.database.BeginSelection(ctx, session, key)
	if errors.Is(err, store.ErrConflict) {
		return jwxkSelectionUnknown(), nil
	}
	if err != nil {
		return jwxkSelectionUnknown(), err
	}
	result := academic.SendJWXKSelection(ctx, runtime, mode, prepared, input.ConfirmRisk)
	state := result.State
	if state == "accepted" || state == "unknown" {
		verified, _, verifyErr := academic.VerifyJWXKSelection(ctx, runtime, mode, input)
		if verifyErr == nil && verified {
			result = academic.JWXKMutationResult{State: "confirmed", Success: true, Code: "200", Message: "已核验官方结果，选课成功"}
			state = "confirmed"
		} else {
			state = "unknown"
			result.State, result.Success, result.Failure = "unknown", false, "MUTATION_UNCERTAIN"
			result.Message = "学校操作结果尚未确认，请刷新官方结果；不会自动重复提交"
		}
	}
	persistCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	if err := api.database.FinishSelection(persistCtx, session, key, attempt, state); err != nil {
		return jwxkSelectionUnknown(), nil
	}
	if state == "confirmed" {
		if err := api.rearmConfirmedSelectionOperation(persistCtx, session, jwxkDeselectionKey(input.Batch, input.Class)); err != nil {
			return result, err
		}
	}
	if state == "confirmed" || state == "unknown" {
		if term := prepared.Term(); term != "" {
			if err := api.database.InvalidateCache(persistCtx, session, "personal-timetable", "term:"+term); err != nil {
				return result, err
			}
			api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+term, "jwxk.select", true)
		}
	}
	return result, nil
}

func (api *API) jwxkSelect(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.JWXKSelect
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "选课参数无效"})
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
	result, err := api.selectJWXKCourse(ctx, runtime, session, mode, input)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}
