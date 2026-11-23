package application

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type detailState struct {
	mutex    sync.Mutex
	warming  bool
	last     time.Time
	blocked  time.Time
	failures int
}

func scoreDetailVariant(code, term string) string {
	digest := sha256.Sum256([]byte(code + "\x1f" + term))
	return "course-" + hex.EncodeToString(digest[:])[:32]
}

func (api *API) detailState(account string) *detailState {
	value, _ := api.detailStates.LoadOrStore(account, &detailState{})
	return value.(*detailState)
}

func (state *detailState) before(ctx context.Context) error {
	state.mutex.Lock()
	if time.Now().Before(state.blocked) {
		state.mutex.Unlock()
		return &campus.Error{Code: "SCORE_DETAIL_THROTTLED", Message: "成绩详情查询暂时受限，请稍后重试"}
	}
	delay := time.Until(state.last.Add(1400 * time.Millisecond))
	state.mutex.Unlock()
	if delay > 0 {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	state.mutex.Lock()
	state.last = time.Now()
	state.mutex.Unlock()
	return nil
}

func (state *detailState) complete(success bool) {
	state.mutex.Lock()
	defer state.mutex.Unlock()
	if success {
		state.failures = 0
		state.blocked = time.Time{}
		return
	}
	delays := []time.Duration{time.Minute, 5 * time.Minute, 30 * time.Minute}
	level := state.failures
	if level >= len(delays) {
		level = len(delays) - 1
	}
	state.blocked = time.Now().Add(delays[level])
	state.failures++
}

func (api *API) cachedScore(ctx context.Context, session store.Session, variant string) (academic.Score, error) {
	entry, err := api.database.ReadCache(ctx, session, "scores", "default")
	if err != nil {
		return academic.Score{}, err
	}
	var scores academic.Scores
	if err := json.Unmarshal(entry.Payload, &scores); err != nil {
		return academic.Score{}, err
	}
	for _, score := range scores.Scores {
		if scoreDetailVariant(score.Code, score.Term) == variant {
			return score, nil
		}
	}
	return academic.Score{}, store.ErrMissing
}

func (api *API) registerScoreDetailResource() {
	api.resources.Register("score-details", ResourceSpec{TTL: 365 * 24 * time.Hour, Offline: true, Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		session := store.Session{Account: runtime.Client.Account(), Generation: runtime.Generation}
		score, err := api.cachedScore(ctx, session, variant)
		if err != nil {
			return nil, nil, err
		}
		state := api.detailState(session.Account)
		if err := state.before(ctx); err != nil {
			return nil, nil, err
		}
		payload, err := academic.FetchScoreDetail(ctx, runtime, score)
		if ctx.Err() == nil {
			state.complete(err == nil)
		}
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(payload)
		return encoded, nil, err
	}})
}

func (api *API) warmScoreDetails(session store.Session, payload json.RawMessage) {
	var scores academic.Scores
	if json.Unmarshal(payload, &scores) != nil {
		return
	}
	runtime := api.accounts.Runtime(session)
	if runtime == nil {
		return
	}
	state := api.detailState(session.Account)
	state.mutex.Lock()
	if state.warming {
		state.mutex.Unlock()
		return
	}
	state.warming = true
	state.mutex.Unlock()
	api.accounts.workers.Add(1)
	go func() {
		defer api.accounts.workers.Done()
		defer func() { state.mutex.Lock(); state.warming = false; state.mutex.Unlock() }()
		for _, score := range scores.Scores {
			if runtime.Context.Err() != nil {
				return
			}
			if score.DetailRef == "" {
				continue
			}
			variant := scoreDetailVariant(score.Code, score.Term)
			if entry, err := api.database.ReadCache(runtime.Context, session, "score-details", variant); err == nil {
				var detail map[string]any
				if json.Unmarshal(entry.Payload, &detail) == nil && detail["source_score"] == score.Score && academic.Number(detail["source_gpa"]) == score.GPA {
					if items, ok := detail["item_scores"].([]any); ok && len(items) > 0 {
						continue
					}
				}
			}
			result, err := api.resources.Submit(runtime.Context, session, "score-details", variant, "score_backfill", true)
			if err != nil {
				return
			}
			identifier, _ := result["job_id"].(string)
			if identifier == "" {
				return
			}
			job, ok := api.resources.Job(session, identifier)
			if !ok {
				return
			}
			select {
			case <-job.done:
			case <-runtime.Context.Done():
				return
			}
			job, _ = api.resources.Job(session, identifier)
			if job.Status != "completed" {
				return
			}
		}
	}()
}

func (api *API) warmCachedScoreDetails(session store.Session) {
	runtime := api.accounts.Runtime(session)
	if runtime == nil || runtime.Context.Err() != nil {
		return
	}
	entry, err := api.database.ReadCache(runtime.Context, session, "scores", "default")
	if err == nil {
		api.warmPolicyScores(session, entry.Payload)
	}
	if err == nil && !entry.Stale(scoresTTL) {
		api.warmScoreDetails(session, entry.Payload)
	}
}

func (api *API) registerScoreDetails(router *http.ServeMux) {
	router.HandleFunc("GET /api/scores/details/cache", api.scoreDetailCache)
	router.HandleFunc("POST /api/scores/details/query", api.scoreDetailRefresh)
}

func (api *API) scoreDetailCache(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	code, term := request.URL.Query().Get("course_code"), request.URL.Query().Get("term")
	if code == "" || len(code) > 128 || term == "" || len(term) > 64 {
		writeJSON(response, 422, map[string]string{"detail": "无效课程或学期"})
		return
	}
	variant := scoreDetailVariant(code, term)
	entry, err := api.database.ReadCache(request.Context(), session, "score-details", variant)
	if err != nil {
		api.documentError(response, err)
		return
	}
	var detail map[string]any
	if err := json.Unmarshal(entry.Payload, &detail); err != nil {
		api.resourceError(response, err)
		return
	}
	score, err := api.cachedScore(request.Context(), session, variant)
	stale := err != nil || detail["source_score"] != score.Score || academic.Number(detail["source_gpa"]) != score.GPA
	delete(detail, "source_score")
	delete(detail, "source_gpa")
	metadata := entry.Metadata(365 * 24 * time.Hour)
	stale = stale || entry.Stale(365*24*time.Hour)
	metadata["is_stale"] = stale
	detail["cached_at"], detail["is_stale"], detail["cache"] = entry.SavedAt, stale, metadata
	writeJSON(response, 200, detail)
}

func (api *API) scoreDetailRefresh(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Code string `json:"course_code"`
		Term string `json:"term"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Code == "" || len(input.Code) > 128 || input.Term == "" || len(input.Term) > 64 {
		writeJSON(response, 422, map[string]string{"detail": "无效课程或学期"})
		return
	}
	variant := scoreDetailVariant(input.Code, input.Term)
	if score, err := api.cachedScore(request.Context(), session, variant); err != nil {
		api.documentError(response, err)
		return
	} else if score.DetailRef == "" {
		writeJSON(response, 409, map[string]string{"detail": "课程详情标识不可用，请先刷新总成绩"})
		return
	}
	result, err := api.resources.Submit(request.Context(), session, "score-details", variant, "manual", true)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}
