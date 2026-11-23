package application

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type ResourceSpec struct {
	AuthScope     string
	SchemaVersion int
	TTL           time.Duration
	Offline       bool
	PublicRefresh bool
	Invalidations []string
	Dependencies  []string
	Fetch         func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error)
	FetchReason   func(context.Context, *Runtime, string, string) (json.RawMessage, json.RawMessage, error)
	After         func(store.Session, json.RawMessage)
	AfterReason   func(store.Session, json.RawMessage, string)
	Diff          func(json.RawMessage, json.RawMessage) map[string]any
}

type CacheJob struct {
	AuthScope        string         `json:"auth_scope,omitempty"`
	ID               string         `json:"job_id"`
	Resource         string         `json:"resource"`
	Variant          string         `json:"variant"`
	Status           string         `json:"status"`
	Reason           string         `json:"reason"`
	CreatedAt        time.Time      `json:"created_at"`
	UpdatedAt        time.Time      `json:"updated_at"`
	PreviousRevision string         `json:"previous_revision"`
	Revision         string         `json:"revision"`
	Changed          bool           `json:"changed"`
	Diff             map[string]any `json:"diff"`
	ErrorKind        string         `json:"error_kind"`
	account          string
	generation       int64
	done             chan struct{}
	obsolete         bool
}

type Resources struct {
	mutex        sync.Mutex
	database     *store.Store
	accounts     *Accounts
	specs        map[string]ResourceSpec
	jobs         map[string]*CacheJob
	active       map[string]string
	accountSlots map[string]chan struct{}
	slots        chan struct{}
}

func NewResources(database *store.Store, accounts *Accounts) *Resources {
	return &Resources{database: database, accounts: accounts, specs: map[string]ResourceSpec{}, jobs: map[string]*CacheJob{}, active: map[string]string{}, accountSlots: map[string]chan struct{}{}, slots: make(chan struct{}, 4)}
}

func (resources *Resources) Register(name string, spec ResourceSpec) { resources.specs[name] = spec }

func (resources *Resources) acquireAccount(ctx context.Context, session store.Session) (func(), error) {
	resources.mutex.Lock()
	slot := resources.accountSlots[session.Account]
	if slot == nil {
		slot = make(chan struct{}, 1)
		resources.accountSlots[session.Account] = slot
	}
	resources.mutex.Unlock()
	select {
	case slot <- struct{}{}:
		return func() { <-slot }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (resources *Resources) Submit(ctx context.Context, session store.Session, resource, variant, reason string, force bool) (map[string]any, error) {
	spec, exists := resources.specs[resource]
	if !exists {
		return nil, store.ErrMissing
	}
	runtime := resources.accounts.Runtime(session)
	if runtime == nil {
		return nil, store.ErrAccess
	}
	if active, _ := runtime.Client.Status(); !active {
		return nil, &campus.Error{Code: "AUTH_EXPIRED", Message: "请先恢复学校登录"}
	}
	resources.mutex.Lock()
	defer resources.mutex.Unlock()
	entry, readErr := resources.database.ReadCache(ctx, session, resource, variant)
	if readErr != nil && !errors.Is(readErr, store.ErrMissing) {
		return nil, readErr
	}
	schema := spec.SchemaVersion
	if schema == 0 {
		schema = 1
	}
	if !force && readErr == nil && entry.SchemaVersion == schema && !entry.Stale(spec.TTL) {
		return map[string]any{"status": "fresh", "resource": resource, "variant": variant, "revision": entry.Revision, "is_stale": false}, nil
	}
	key := session.Account + "\x00" + strconv.FormatInt(session.Generation, 10) + "\x00" + resource + "\x00" + variant
	if id := resources.active[key]; id != "" {
		return map[string]any{"status": "running", "resource": resource, "variant": variant, "job_id": id, "is_stale": true}, nil
	}
	for id, job := range resources.jobs {
		if job.Status != "running" && job.Status != "queued" && time.Since(job.UpdatedAt) > time.Hour {
			delete(resources.jobs, id)
		}
		if !job.obsolete && job.account == session.Account && job.Resource == resource && job.Variant == variant && job.generation == session.Generation {
			if job.Status == "failed" && time.Since(job.UpdatedAt) < time.Minute {
				return map[string]any{"status": "throttled", "resource": resource, "variant": variant, "error_kind": job.ErrorKind, "retry_after_seconds": 60, "is_stale": true}, nil
			}
			if job.Status == "completed" && time.Since(job.UpdatedAt) < 2*time.Second && !entry.Invalidated {
				return map[string]any{"status": "running", "resource": resource, "variant": variant, "job_id": id, "is_stale": false}, nil
			}
		}
	}
	if len(resources.active) >= 256 {
		return nil, &campus.Error{Code: "BUSY", Message: "后台任务繁忙，请稍后刷新"}
	}
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return nil, err
	}
	job := &CacheJob{ID: hex.EncodeToString(identifier), Resource: resource, Variant: variant, Status: "queued", Reason: reason, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(), PreviousRevision: entry.Revision, account: session.Account, generation: session.Generation, done: make(chan struct{}), Diff: map[string]any{}}
	resources.jobs[job.ID], resources.active[key] = job, job.ID
	job.AuthScope = spec.AuthScope
	accountSlot := resources.accountSlots[session.Account]
	if accountSlot == nil {
		accountSlot = make(chan struct{}, 1)
		resources.accountSlots[session.Account] = accountSlot
	}
	resources.accounts.workers.Add(1)
	go resources.run(session, runtime, spec, job, key, accountSlot)
	return map[string]any{"status": "started", "resource": resource, "variant": variant, "job_id": job.ID, "revision": entry.Revision, "is_stale": true}, nil
}

func (resources *Resources) run(session store.Session, runtime *Runtime, spec ResourceSpec, job *CacheJob, key string, accountSlot chan struct{}) {
	defer resources.accounts.workers.Done()
	status, kind, revision, changed := "cancelled", "identity_changed", "", false
	var committedPayload json.RawMessage
	diff := map[string]any{"initial": job.PreviousRevision == ""}
	defer func() {
		resources.mutex.Lock()
		job.Status, job.ErrorKind, job.Revision, job.Changed, job.UpdatedAt = status, kind, revision, changed, time.Now().UTC()
		job.Diff = diff
		if job.obsolete {
			status, kind = "cancelled", "resource_deleted"
			job.Status, job.ErrorKind = status, kind
		}
		if resources.active[key] == job.ID {
			delete(resources.active, key)
		}
		close(job.done)
		resources.mutex.Unlock()
		if status == "completed" && spec.After != nil {
			spec.After(session, committedPayload)
		}
		if status == "completed" && spec.AfterReason != nil {
			spec.AfterReason(session, committedPayload, job.Reason)
		}
		resources.logRefresh(session, job, status, changed)
	}()
	ctx, cancel := context.WithTimeout(runtime.Context, 3*time.Minute)
	defer cancel()
	select {
	case accountSlot <- struct{}{}:
		defer func() { <-accountSlot }()
	case <-ctx.Done():
		return
	}
	select {
	case resources.slots <- struct{}{}:
		defer func() { <-resources.slots }()
	case <-ctx.Done():
		return
	}
	resources.mutex.Lock()
	if job.obsolete {
		resources.mutex.Unlock()
		kind = "resource_deleted"
		return
	}
	job.Status, job.UpdatedAt = "running", time.Now().UTC()
	resources.mutex.Unlock()
	resources.logRefresh(session, job, "started", false)
	var payload, semantic json.RawMessage
	var err error
	if spec.FetchReason != nil {
		payload, semantic, err = spec.FetchReason(ctx, runtime, job.Variant, job.Reason)
	} else {
		payload, semantic, err = spec.Fetch(ctx, runtime, job.Variant)
	}
	if err == nil {
		previous, readErr := resources.database.ReadCache(ctx, session, job.Resource, job.Variant)
		if readErr != nil && !errors.Is(readErr, store.ErrMissing) {
			err = readErr
		}
		if spec.Diff != nil {
			diff = spec.Diff(previous.Payload, payload)
		}
		dependencies := map[string]string{}
		for _, resource := range spec.Dependencies {
			entry, readErr := resources.database.ReadCache(ctx, session, resource, "default")
			if readErr == nil {
				dependencies[resource] = entry.Revision
			} else if !errors.Is(readErr, store.ErrMissing) {
				err = readErr
				break
			}
		}
		if semantic == nil {
			semantic = payload
		}
		digest := sha256.Sum256(semantic)
		revision = "v1:" + hex.EncodeToString(digest[:])
		if err == nil {
			schema := spec.SchemaVersion
			if schema == 0 {
				schema = 1
			}
			changed, err = resources.database.CommitCache(ctx, session, store.CacheEntry{Resource: job.Resource, Variant: job.Variant, SchemaVersion: schema, Payload: payload, Revision: revision, Dependencies: dependencies, Changes: diff}, job.Reason, spec.Invalidations)
		}
	}
	if err == nil {
		status, kind, committedPayload = "completed", "", payload
		return
	}
	if runtime.Context.Err() != nil || errors.Is(err, store.ErrAccess) {
		return
	}
	status, kind = "failed", "UPSTREAM_RESPONSE_INVALID"
	var failure *campus.Error
	if errors.As(err, &failure) {
		kind = failure.Code
	}
	resources.database.CacheFailure(ctx, session, job.Resource, job.Variant, kind)
}

func (resources *Resources) logRefresh(session store.Session, job *CacheJob, status string, changed bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	level := "INFO"
	if status == "failed" {
		level = "ERROR"
	}
	resources.database.AppendSessionDiagnostic(ctx, session, store.DiagnosticEntry{Category: "sync", Level: level, EventType: "cache_refresh_" + status, EventTitle: "缓存刷新", Summary: job.Resource + " · " + status, Details: map[string]any{"job_id": job.ID, "resource": job.Resource, "status": status, "changed": changed}})
}

func (resources *Resources) Delete(ctx context.Context, session store.Session, resource, variant string) (bool, int, error) {
	release, err := resources.acquireAccount(ctx, session)
	if err != nil {
		return false, 0, err
	}
	defer release()
	resources.mutex.Lock()
	defer resources.mutex.Unlock()
	deleted, err := resources.database.DeleteCacheForSession(ctx, session, resource, variant)
	if err != nil {
		return false, 0, err
	}
	cancelled := 0
	for _, job := range resources.jobs {
		if job.account != session.Account || job.generation != session.Generation || job.Resource != resource || job.Variant != variant {
			continue
		}
		job.obsolete = true
		if job.Status == "running" || job.Status == "queued" {
			cancelled++
		}
	}
	key := session.Account + "\x00" + strconv.FormatInt(session.Generation, 10) + "\x00" + resource + "\x00" + variant
	delete(resources.active, key)
	return deleted, cancelled, nil
}

func (resources *Resources) Job(session store.Session, id string) (CacheJob, bool) {
	resources.mutex.Lock()
	defer resources.mutex.Unlock()
	job := resources.jobs[id]
	if job == nil || job.account != session.Account || job.generation != session.Generation {
		return CacheJob{}, false
	}
	return *job, true
}

func (api *API) registerCache(router *http.ServeMux) {
	router.HandleFunc("GET /api/system-settings/cache", api.cacheSettings)
	router.HandleFunc("PUT /api/system-settings/cache", api.cacheSettings)
	router.HandleFunc("POST /api/cache/refresh/{resource}", api.refreshResource)
	router.HandleFunc("GET /api/cache/jobs/{id}", func(response http.ResponseWriter, request *http.Request) {
		session, ok := api.requireSession(response, request)
		if !ok {
			return
		}
		job, ok := api.resources.Job(session, request.PathValue("id"))
		if !ok {
			api.documentError(response, store.ErrMissing)
			return
		}
		writeJSON(response, 200, job)
	})
	router.HandleFunc("GET /api/cache/events", api.cacheEvents)
}

func (api *API) refreshResource(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	resource := request.PathValue("resource")
	spec, exists := api.resources.specs[resource]
	if !exists || !spec.PublicRefresh {
		api.documentError(response, store.ErrMissing)
		return
	}
	if variant := request.URL.Query().Get("variant"); variant != "" && variant != "default" {
		writeJSON(response, 422, map[string]string{"detail": "该资源不支持自定义缓存变体"})
		return
	}
	reason := request.URL.Query().Get("reason")
	if reason == "" {
		reason = "page_swr"
	}
	if reason != "manual" && reason != "page_swr" {
		writeJSON(response, 422, map[string]string{"detail": "无效刷新原因"})
		return
	}
	result, err := api.resources.Submit(request.Context(), session, resource, "default", reason, request.URL.Query().Get("force") == "true")
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}

func (api *API) resourceError(response http.ResponseWriter, err error) {
	var failure *campus.Error
	if errors.As(err, &failure) {
		status := 503
		if failure.Code == "AUTH_EXPIRED" || failure.Code == "AUTH_REQUIRED" {
			status = 401
		} else if failure.Code == "INVALID_CURSOR" {
			status = 422
		} else if failure.Code == "WEIGHT_PLAN_INVALID" {
			status = 422
		} else if failure.Code == "JWXK_PREFLIGHT_FAILED" || failure.Code == "JWXK_BATCH_INELIGIBLE" || failure.Code == "JWXK_BATCH_MISSING" {
			status = 409
		}
		writeJSON(response, status, map[string]string{"detail": failure.Message, "error_code": failure.Code})
		return
	}
	api.documentError(response, err)
}

func (api *API) eventSnapshot(request *http.Request, session store.Session, parameter string) (map[string]any, error) {
	var after *int64
	if value := request.URL.Query().Get(parameter); value != "" {
		parsed, err := strconv.ParseInt(value, 10, 64)
		if err != nil || parsed < 0 {
			return nil, &campus.Error{Code: "INVALID_CURSOR", Message: "无效事件游标"}
		}
		after = &parsed
	}
	events, cursor, err := api.database.CacheEvents(request.Context(), session, after, 200)
	return map[string]any{"events": events, "cursor": cursor}, err
}

func (api *API) cacheEvents(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	result, err := api.eventSnapshot(request, session, "after")
	if err != nil {
		api.resourceError(response, err)
		return
	}
	writeJSON(response, 200, result)
}
