package application

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

const avatarTTL = 24 * time.Hour

func (api *API) registerUserResource() {
	api.resources.Register("avatar", ResourceSpec{TTL: avatarTTL, PublicRefresh: true, Fetch: func(ctx context.Context, runtime *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		payload, err := academic.FetchAvatar(ctx, runtime, runtime.Client.Account())
		if err != nil {
			return nil, nil, err
		}
		encoded, err := json.Marshal(payload)
		return encoded, nil, err
	}})
}

func (api *API) avatarSnapshot(ctx context.Context, session store.Session) any {
	entry, err := api.database.ReadCache(ctx, session, "avatar", "default")
	if err != nil {
		return nil
	}
	var payload map[string]any
	if json.Unmarshal(entry.Payload, &payload) != nil {
		return nil
	}
	payload["revision"], payload["stale"], payload["saved_at"], payload["last_checked_at"] = entry.Revision, entry.Stale(avatarTTL), entry.SavedAt, entry.CheckedAt
	return payload
}

func (api *API) registerUser(router *http.ServeMux) {
	router.HandleFunc("GET /api/user/info", func(response http.ResponseWriter, request *http.Request) {
		runtime := api.outlineRuntime(response, request)
		if runtime == nil {
			return
		}
		payload, err := academic.FetchUserInfo(request.Context(), runtime, runtime.Client.Account())
		if err != nil {
			api.resourceError(response, err)
			return
		}
		writeJSON(response, 200, payload)
	})
	router.HandleFunc("GET /api/user/avatar/cache", api.userAvatar)
	router.HandleFunc("GET /api/user/avatar", api.userAvatar)
}

func (api *API) userAvatar(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	entry, err := api.database.ReadCache(request.Context(), session, "avatar", "default")
	force := request.URL.Query().Get("refresh") == "true"
	if request.URL.Path == "/api/user/avatar" && (err != nil || force || entry.Stale(avatarTTL)) {
		result, submitErr := api.resources.Submit(request.Context(), session, "avatar", "default", "page_swr", force)
		if submitErr == nil && (err != nil || force) {
			identifier, _ := result["job_id"].(string)
			if job, found := api.resources.Job(session, identifier); found {
				timer := time.NewTimer(8 * time.Second)
				select {
				case <-job.done:
				case <-timer.C:
				case <-request.Context().Done():
					timer.Stop()
					return
				}
				timer.Stop()
			}
			entry, err = api.database.ReadCache(request.Context(), session, "avatar", "default")
		}
	}
	response.Header().Set("Cache-Control", "no-store")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	if errors.Is(err, store.ErrMissing) {
		response.Header().Set("X-Cache-Miss", "true")
		response.Header().Set("X-Avatar-Unavailable", "true")
		response.WriteHeader(204)
		return
	}
	if err != nil {
		api.resourceError(response, err)
		return
	}
	var payload struct {
		Image string `json:"image_base64"`
		Media string `json:"media_type"`
		Token string `json:"token"`
	}
	if err := json.Unmarshal(entry.Payload, &payload); err != nil {
		api.resourceError(response, err)
		return
	}
	image, err := base64.StdEncoding.DecodeString(payload.Image)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	response.Header().Set("Content-Type", payload.Media)
	response.Header().Set("ETag", `"`+entry.Revision+`"`)
	response.Header().Set("X-Cache-Stale", strconv.FormatBool(entry.Stale(avatarTTL)))
	response.Header().Set("X-Avatar-Token", payload.Token)
	response.Header().Set("X-Cache-Saved-At", entry.SavedAt)
	response.Header().Set("X-Cache-Last-Checked-At", entry.CheckedAt)
	response.WriteHeader(200)
	response.Write(image)
}
