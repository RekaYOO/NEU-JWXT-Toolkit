package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const scoresTTL = 5 * time.Minute

func (api *API) registerAcademicResources() {
	api.resources.Register("scores", ResourceSpec{TTL: scoresTTL, Offline: true, PublicRefresh: true, Invalidations: []string{"academic-report"}, AfterReason: api.afterScoresRefresh, Diff: academic.ScoresDiff, Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		scores, err := academic.FetchScores(ctx, runtime)
		if err != nil {
			return nil, nil, err
		}
		payload, err := json.Marshal(scores)
		if err != nil {
			return nil, nil, err
		}
		semantic, err := json.Marshal(scores.Semantic())
		return payload, semantic, err
	}})
	api.registerScoreDetailResource()
	api.resources.Register("academic-report", ResourceSpec{TTL: scoresTTL, Offline: true, PublicRefresh: true, Dependencies: []string{"scores"}, Diff: academic.ReportDiff, Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		report, err := academic.FetchReport(ctx, runtime, runtime.Client.Account())
		if err != nil {
			return nil, nil, err
		}
		payload, err := json.Marshal(report)
		if err != nil {
			return nil, nil, err
		}
		semantic, err := academic.ReportSemantic(report)
		return payload, semantic, err
	}})
}

func (api *API) registerScores(router *http.ServeMux) {
	router.HandleFunc("GET /api/scores/cache", api.cachedScores)
	router.HandleFunc("GET /api/scores", api.onlineScores)
	router.HandleFunc("POST /api/scores/refresh", func(response http.ResponseWriter, request *http.Request) {
		api.refreshAndWait(response, request, "scores")
	})
	router.HandleFunc("GET /api/scores/by-term", api.scoresByTerm)
	router.HandleFunc("GET /api/gpa-policy", api.getGPAPolicy)
	router.HandleFunc("PUT /api/gpa-policy", api.saveGPAPolicy)
	api.registerScoreDetails(router)
}

func (api *API) scorePayload(ctx context.Context, session store.Session, entry store.CacheEntry) (map[string]any, error) {
	var scores academic.Scores
	if err := json.Unmarshal(entry.Payload, &scores); err != nil {
		return nil, err
	}
	policy := api.gpaPolicy(ctx, session, scores.Scores)
	average, _, _ := gpaSummary(scores.Scores, policy)
	return map[string]any{"scores": scores.Semantic().Scores, "total_courses": len(scores.Scores), "overall_gpa": scores.OverallGPA, "official_major_rank": scores.OfficialMajorRank, "calculated_gpa": average, "gpa_policy": policy,
		"source": "local", "is_fresh": !entry.Stale(scoresTTL), "last_update": entry.SavedAt, "cache": entry.Metadata(scoresTTL)}, nil
}

func (api *API) cachedScores(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "scores", "default")
	if err != nil {
		api.documentError(response, err)
		return
	}
	payload, err := api.scorePayload(request.Context(), session, entry)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	if request.Context().Value(offlineIdentityKey{}) != nil {
		payload["source"], payload["is_fresh"] = "offline", false
	}
	writeJSON(response, 200, payload)
}

func (api *API) onlineScores(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "scores", "default")
	force := request.URL.Query().Get("refresh") == "true"
	if err != nil || force || entry.Stale(scoresTTL) {
		result, submitErr := api.resources.Submit(request.Context(), session, "scores", "default", "page_swr", force)
		if submitErr != nil && err != nil {
			api.resourceError(response, submitErr)
			return
		}
		if err != nil || force {
			if identifier, ok := result["job_id"].(string); ok {
				if job, found := api.resources.Job(session, identifier); found {
					timer := time.NewTimer(25 * time.Second)
					select {
					case <-job.done:
					case <-timer.C:
					case <-request.Context().Done():
						timer.Stop()
						return
					}
					timer.Stop()
				}
			}
		}
	}
	api.cachedScores(response, request)
}

func (api *API) scoresByTerm(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "scores", "default")
	if err != nil {
		api.documentError(response, err)
		return
	}
	var scores academic.Scores
	if err := json.Unmarshal(entry.Payload, &scores); err != nil {
		api.resourceError(response, err)
		return
	}
	groups := map[string][]academic.Score{}
	for _, score := range scores.Semantic().Scores {
		groups[score.Term] = append(groups[score.Term], score)
	}
	keys := []string{}
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Sort(sort.Reverse(sort.StringSlice(keys)))
	policy := api.gpaPolicy(request.Context(), session, scores.Scores)
	result := []map[string]any{}
	for _, key := range keys {
		courses := groups[key]
		average, _, _ := gpaSummary(courses, policy)
		credits := 0.0
		for _, course := range courses {
			credits += course.Credit
		}
		result = append(result, map[string]any{"term_code": key, "term_name": courses[0].TermDisplay, "courses": courses, "total_credits": credits, "gpa": average})
	}
	writeJSON(response, 200, result)
}

func (api *API) gpaPolicy(ctx context.Context, session store.Session, scores []academic.Score) map[string]any {
	defaultMode := "through_2024"
	if len(session.Account) >= 4 {
		if year, err := strconv.Atoi(session.Account[:4]); err == nil && year >= 2025 {
			defaultMode = "from_2025"
		}
	}
	var override *string
	if document, err := api.database.Read(ctx, session.Account, "gpa-policy", "default"); err == nil && !document.Deleted {
		var saved struct {
			Mode *string `json:"mode"`
		}
		if json.Unmarshal(document.Payload, &saved) == nil && saved.Mode != nil && (*saved.Mode == "through_2024" || *saved.Mode == "from_2025") {
			override = saved.Mode
		}
	}
	mode := defaultMode
	if override != nil {
		mode = *override
	}
	excluded, codes := map[string]bool{}, map[string]bool{}
	for _, score := range scores {
		codes[score.Code] = true
		if strings.Contains(score.CourseCategory, "通识选修") {
			excluded[score.Code] = true
		}
	}
	reportAvailable := false
	if entry, err := api.database.ReadCache(ctx, session, "academic-report", "default"); err == nil {
		var report map[string]any
		if json.Unmarshal(entry.Payload, &report) == nil {
			if nested := academic.Object(report["report"]); nested != nil {
				report = nested
			}
			var walk func([]any, bool)
			walk = func(nodes []any, parentExcluded bool) {
				for _, value := range nodes {
					node := academic.Object(value)
					blocked := parentExcluded || strings.Contains(academic.Text(node["name"]), "通识选修")
					if rows, ok := node["courses"].([]any); ok {
						for _, row := range rows {
							course := academic.Object(row)
							code := academic.Text(course["course_code"])
							if code == "" {
								code = academic.Text(course["code"])
							}
							if code != "" {
								codes[code] = true
								if blocked || course["gpa_general_elective"] == true {
									excluded[code] = true
								}
							}
						}
					}
					if children, ok := node["children"].([]any); ok {
						walk(children, blocked)
					}
				}
			}
			if categories, ok := report["categories"].([]any); ok {
				reportAvailable = len(categories) > 0
				walk(categories, false)
			}
		}
	}
	scales := map[string]string{}
	for code := range codes {
		if entry, err := api.database.ReadCache(ctx, session, "course-outline-metadata", "course:"+code); err == nil {
			var metadata map[string]any
			if json.Unmarshal(entry.Payload, &metadata) == nil {
				if scale := academic.Text(metadata["grading_scale"]); scale != "" {
					scales[code] = scale
				}
			}
		}
	}
	unknown := 0
	for _, score := range scores {
		if !excluded[score.Code] && scales[score.Code] == "" && score.GradingScale == "" {
			unknown++
		}
	}
	excludedCodes := []string{}
	for code := range excluded {
		excludedCodes = append(excludedCodes, code)
	}
	sort.Strings(excludedCodes)
	return map[string]any{"mode": mode, "default_mode": defaultMode, "override": override, "general_elective_codes": excludedCodes, "grading_scales": scales, "report_available": reportAvailable, "missing_grading_scales": unknown}
}

func gpaSummary(scores []academic.Score, policy map[string]any) (float64, float64, int) {
	excluded := map[string]bool{}
	for _, code := range policy["general_elective_codes"].([]string) {
		excluded[code] = true
	}
	scales := policy["grading_scales"].(map[string]string)
	points, credits, count := 0.0, 0.0, 0
	for _, score := range scores {
		if score.Credit <= 0 || score.GPA < 0 {
			continue
		}
		scale := scales[score.Code]
		if scale == "" {
			scale = score.GradingScale
		}
		if policy["mode"] == "from_2025" && (excluded[score.Code] || strings.Contains(score.CourseCategory, "通识选修") || strings.TrimSpace(scale) == "两级制") {
			continue
		}
		points += score.Credit * score.GPA
		credits += score.Credit
		count++
	}
	if credits == 0 {
		return 0, 0, 0
	}
	return points / credits, credits, count
}

func (api *API) cachedPolicyScores(ctx context.Context, session store.Session) []academic.Score {
	var scores academic.Scores
	if entry, err := api.database.ReadCache(ctx, session, "scores", "default"); err == nil {
		json.Unmarshal(entry.Payload, &scores)
	}
	return scores.Scores
}

func (api *API) policyForSession(ctx context.Context, session store.Session) map[string]any {
	return api.gpaPolicy(ctx, session, api.cachedPolicyScores(ctx, session))
}

func missingPolicyCodes(scores []academic.Score, policy map[string]any) []string {
	seen := map[string]bool{}
	for _, code := range policy["general_elective_codes"].([]string) {
		seen[code] = true
	}
	scales := policy["grading_scales"].(map[string]string)
	var missing []string
	for _, score := range scores {
		if !seen[score.Code] && academic.ValidCourseCode(score.Code) && scales[score.Code] == "" && score.GradingScale == "" {
			seen[score.Code] = true
			missing = append(missing, score.Code)
		}
	}
	sort.Strings(missing)
	return missing
}

func (api *API) getGPAPolicy(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	writeJSON(response, 200, api.policyForSession(request.Context(), session))
}

func (api *API) saveGPAPolicy(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Mode *string `json:"mode"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	if input.Mode != nil && *input.Mode != "through_2024" && *input.Mode != "from_2025" {
		writeJSON(response, 422, map[string]string{"detail": "无效绩点策略"})
		return
	}
	previous, err := api.database.Read(request.Context(), session.Account, "gpa-policy", "default")
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	payload, _ := json.Marshal(input)
	if _, err := api.database.SaveForSession(request.Context(), session, "gpa-policy", "default", payload, previous.Revision, false); err != nil {
		api.documentError(response, err)
		return
	}
	ctx := request.Context()
	scores := api.cachedPolicyScores(ctx, session)
	policy := api.gpaPolicy(ctx, session, scores)
	api.queuePolicyContext(ctx, session, scores, policy)
	writeJSON(response, 200, policy)
}

func (api *API) queuePolicyContext(ctx context.Context, session store.Session, scores []academic.Score, policy map[string]any) {
	if policy["mode"] == "from_2025" {
		api.resources.Submit(ctx, session, "academic-report", "default", "gpa_context", false)
		for _, code := range missingPolicyCodes(scores, policy) {
			api.resources.Submit(ctx, session, "course-outline-metadata", "course:"+code, "gpa_context", false)
		}
	}
}

func (api *API) warmPolicyScores(session store.Session, payload json.RawMessage) {
	runtime := api.accounts.Runtime(session)
	var scores academic.Scores
	if runtime == nil || runtime.Context.Err() != nil || json.Unmarshal(payload, &scores) != nil {
		return
	}
	api.queuePolicyContext(runtime.Context, session, scores.Scores, api.gpaPolicy(runtime.Context, session, scores.Scores))
}
