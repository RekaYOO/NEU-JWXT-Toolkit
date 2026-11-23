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

func jwxkDeselectionKey(batch, class string) string {
	encoded, _ := json.Marshal([]string{"deselect", batch, class})
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func (api *API) rearmConfirmedSelectionOperation(ctx context.Context, session store.Session, key string) error {
	previous, err := api.database.SelectionAttempt(ctx, session, key)
	if errors.Is(err, store.ErrMissing) {
		return nil
	}
	if err != nil {
		return err
	}
	if previous.State != "confirmed" {
		return nil
	}
	return api.database.FinishSelection(ctx, session, key, previous, "not_sent")
}

func (api *API) completeJWXKDeselection(ctx context.Context, runtime *Runtime, session store.Session, input academic.JWXKDeselect, attempt store.SelectionAttempt, term string) error {
	if attempt.Course != "" {
		if err := api.rearmConfirmedSelectionOperation(ctx, session, jwxkSelectionKey(academic.JWXKSelect{Batch: input.Batch, Course: attempt.Course})); err != nil {
			return err
		}
	}
	if term == "" {
		term = attempt.Term
	}
	if term != "" {
		if err := api.database.InvalidateCache(ctx, session, "personal-timetable", "term:"+term); err != nil {
			return err
		}
		api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+term, "jwxk.deselect", true)
	}
	return nil
}

func (api *API) deselectJWXKCourse(ctx context.Context, runtime *Runtime, session store.Session, mode string, input academic.JWXKDeselect) (academic.JWXKMutationResult, error) {
	key := jwxkDeselectionKey(input.Batch, input.Class)
	previous, err := api.database.SelectionAttempt(ctx, session, key)
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return jwxkSelectionUnknown(), err
	}
	if err == nil && previous.State != "rejected" && previous.State != "not_sent" {
		verified, term, verifyErr := academic.VerifyJWXKDeselection(ctx, runtime, mode, input)
		if verifyErr == nil && verified {
			if err := api.database.FinishSelection(ctx, session, key, previous, "confirmed"); err != nil {
				return jwxkSelectionUnknown(), err
			}
			if err := api.completeJWXKDeselection(ctx, runtime, session, input, previous, term); err != nil {
				return jwxkSelectionUnknown(), err
			}
			return academic.JWXKMutationResult{State: "confirmed", Success: true, Code: "already_removed", Message: "已核验官方结果，课程已退选，未重复提交"}, nil
		}
		return jwxkSelectionUnknown(), nil
	}
	prepared, err := academic.PrepareJWXKDeselection(ctx, runtime, mode, input)
	if err != nil {
		return jwxkSelectionUnknown(), err
	}
	attempt, err := api.database.BeginSelectionOperation(ctx, session, key, prepared.Course(), prepared.Term(), input.Batch, input.Class)
	if errors.Is(err, store.ErrConflict) {
		return jwxkSelectionUnknown(), nil
	}
	if err != nil {
		return jwxkSelectionUnknown(), err
	}
	result := academic.SendJWXKDeselection(ctx, runtime, mode, prepared, input.ConfirmRisk)
	state := result.State
	if state == "accepted" || state == "unknown" {
		verified, _, verifyErr := academic.VerifyJWXKDeselection(ctx, runtime, mode, input)
		if verifyErr == nil && verified {
			result = academic.JWXKMutationResult{State: "confirmed", Success: true, Code: "200", Message: "已核验官方结果，退课成功"}
			state = "confirmed"
		} else {
			state = "unknown"
			result.State, result.Success, result.Failure = "unknown", false, "MUTATION_UNCERTAIN"
			result.Message = "退课结果尚未确认，请刷新官方结果；不会自动重复提交"
		}
	}
	persistCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	if err := api.database.FinishSelection(persistCtx, session, key, attempt, state); err != nil {
		return jwxkSelectionUnknown(), nil
	}
	if state == "confirmed" {
		if err := api.completeJWXKDeselection(persistCtx, runtime, session, input, attempt, prepared.Term()); err != nil {
			return result, err
		}
	}
	if state == "unknown" && prepared.Term() != "" {
		if err := api.database.InvalidateCache(persistCtx, session, "personal-timetable", "term:"+prepared.Term()); err != nil {
			return result, err
		}
		api.resources.Submit(runtime.Context, session, "personal-timetable", "term:"+prepared.Term(), "jwxk.deselect.verify", true)
	}
	return result, nil
}

func (api *API) jwxkDeselect(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input academic.JWXKDeselect
	if !decodeJSON(response, request, &input) {
		return
	}
	if !input.Valid() {
		writeJSON(response, 422, map[string]string{"detail": "退课参数无效"})
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
	result, err := api.deselectJWXKCourse(ctx, runtime, session, mode, input)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}
