package application

import (
	"context"
	"net/http"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type offlineIdentityKey struct{}

func (api *API) offlineIdentity(request *http.Request) (store.Session, error) {
	if session, err := api.session(request); err == nil {
		return session, nil
	}
	if api.accounts.mode != "local-single" {
		return store.Session{}, store.ErrAccess
	}
	return api.database.LatestCachedAccount(request.Context())
}

func (api *API) offlineHandler(handler http.HandlerFunc) http.HandlerFunc {
	return func(response http.ResponseWriter, request *http.Request) {
		session, err := api.offlineIdentity(request)
		if err != nil {
			api.documentError(response, err)
			return
		}
		handler(response, request.WithContext(context.WithValue(request.Context(), offlineIdentityKey{}, session)))
	}
}

func (api *API) registerOffline(router *http.ServeMux) {
	router.HandleFunc("GET /api/offline/status", api.offlineStatus)
	router.HandleFunc("GET /api/offline/scores", api.offlineHandler(api.cachedScores))
	router.HandleFunc("GET /api/offline/scores/details", api.offlineHandler(api.scoreDetailCache))
	router.HandleFunc("GET /api/offline/gpa-policy", api.offlineHandler(api.getGPAPolicy))
	router.HandleFunc("GET /api/offline/academic-report", api.offlineHandler(func(response http.ResponseWriter, request *http.Request) {
		api.readReport(response, request, false, false)
	}))
}

func (api *API) offlineStatus(response http.ResponseWriter, request *http.Request) {
	session, err := api.offlineIdentity(request)
	if err != nil && api.accounts.mode != "local-single" {
		api.documentError(response, err)
		return
	}
	resources := []string{}
	status := map[string]any{"available": false, "has_scores": false, "has_report": false, "has_research": false, "has_system_messages": false, "username": "", "read_only": true}
	if err == nil {
		status["username"] = session.Account
		for _, item := range []struct{ resource, flag string }{{"scores", "has_scores"}, {"academic-report", "has_report"}, {"research-training", "has_research"}, {"system-messages", "has_system_messages"}, {"experiment-courses", "has_experiments"}, {"festival-activities", "has_festival"}} {
			spec, exists := api.resources.specs[item.resource]
			if !exists || !spec.Offline {
				continue
			}
			if entry, err := api.database.ReadCache(request.Context(), session, item.resource, "default"); err == nil && entry.SchemaVersion == 1 {
				resources = append(resources, item.resource)
				status[item.flag] = true
			}
		}
	}
	status["resources"], status["available"] = resources, len(resources) > 0
	writeJSON(response, 200, status)
}
