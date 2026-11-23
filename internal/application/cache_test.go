package application

import (
	"context"
	"encoding/json"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestDocumentWorkWaitsForItsOwnAccountOnly(t *testing.T) {
	resources := NewResources(nil, nil)
	first := store.Session{Account: "first"}
	second := store.Session{Account: "second"}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	release, err := resources.acquireAccount(ctx, first)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	waiting, stop := context.WithCancel(ctx)
	defer stop()
	result := make(chan error, 1)
	go func() {
		result <- resources.documentWorkStep(waiting, first, func() error { return errors.New("overlapping account work") })
	}()
	if err := resources.documentWorkStep(ctx, second, func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	stop()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("waiting account work returned %v", err)
		}
	case <-ctx.Done():
		t.Fatal("waiting work did not stop")
	}
}

func waitTestJob(t *testing.T, resources *Resources, session store.Session, result map[string]any) CacheJob {
	t.Helper()
	identifier, _ := result["job_id"].(string)
	job, found := resources.Job(session, identifier)
	if !found {
		t.Fatalf("missing job: %+v", result)
	}
	select {
	case <-job.done:
	case <-time.After(3 * time.Second):
		t.Fatal("job did not settle")
	}
	job, _ = resources.Job(session, identifier)
	return job
}

func TestResourcesDeduplicatePreserveFailureAndFenceLogout(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "20250001")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	started, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	api.resources.Register("test-resource", ResourceSpec{TTL: time.Hour, Fetch: func(ctx context.Context, runtime *Runtime, variant string) (json.RawMessage, json.RawMessage, error) {
		calls.Add(1)
		close(started)
		select {
		case <-release:
			return json.RawMessage(`{"value":1}`), nil, nil
		case <-ctx.Done():
			return nil, nil, ctx.Err()
		}
	}})
	first, err := api.resources.Submit(context.Background(), session, "test-resource", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	<-started
	second, err := api.resources.Submit(context.Background(), session, "test-resource", "default", "manual", true)
	if err != nil || first["job_id"] != second["job_id"] || calls.Load() != 1 {
		t.Fatalf("duplicate upstream work: %v %v %v", first, second, err)
	}
	if _, found := api.resources.Job(store.Session{Account: "other", Generation: session.Generation}, first["job_id"].(string)); found {
		t.Fatal("job exposed across account boundary")
	}
	close(release)
	if job := waitTestJob(t, api.resources, session, first); job.Status != "completed" {
		t.Fatalf("job: %+v", job)
	}
	fresh, err := api.resources.Submit(context.Background(), session, "test-resource", "default", "page_swr", false)
	if err != nil || fresh["status"] != "fresh" {
		t.Fatalf("fresh cache triggered work: %v %v", fresh, err)
	}
	api.resources.Register("failing", ResourceSpec{TTL: time.Hour, Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		return nil, nil, errors.New("anonymous failure")
	}})
	entry := store.CacheEntry{Resource: "failing", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"value":"preserved"}`), Revision: "old"}
	if _, err := api.database.CommitCache(context.Background(), session, entry, "test", nil); err != nil {
		t.Fatal(err)
	}
	failed, err := api.resources.Submit(context.Background(), session, "failing", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	if job := waitTestJob(t, api.resources, session, failed); job.Status != "failed" {
		t.Fatalf("failure status: %+v", job)
	}
	preserved, err := api.database.ReadCache(context.Background(), session, "failing", "default")
	if err != nil || string(preserved.Payload) != string(entry.Payload) || preserved.ErrorKind == "" {
		t.Fatalf("failed refresh lost data: %+v %v", preserved, err)
	}
	throttled, err := api.resources.Submit(context.Background(), session, "failing", "default", "manual", true)
	if err != nil || throttled["status"] != "throttled" {
		t.Fatalf("missing backoff: %+v %v", throttled, err)
	}
	lateStarted := make(chan struct{})
	api.resources.Register("late", ResourceSpec{Fetch: func(ctx context.Context, _ *Runtime, _ string) (json.RawMessage, json.RawMessage, error) {
		close(lateStarted)
		<-ctx.Done()
		return json.RawMessage(`{"late":true}`), nil, nil
	}})
	late, err := api.resources.Submit(context.Background(), session, "late", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	<-lateStarted
	if err := api.accounts.Stop(context.Background(), session.Account, false); err != nil {
		t.Fatal(err)
	}
	if job := waitTestJob(t, api.resources, session, late); job.Status != "cancelled" {
		t.Fatalf("logout did not cancel job: %+v", job)
	}
	if _, err := api.database.ReadCache(context.Background(), session, "late", "default"); !errors.Is(err, store.ErrMissing) {
		t.Fatalf("late cache persisted: %v", err)
	}
}

func TestResourceInvalidationBypassesCompletedJobCooldown(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	api.resources.Register("test-resource", ResourceSpec{TTL: time.Hour, Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		value := calls.Add(1)
		payload, err := json.Marshal(map[string]int32{"value": value})
		return payload, nil, err
	}})
	first, err := api.resources.Submit(context.Background(), session, "test-resource", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	if job := waitTestJob(t, api.resources, session, first); job.Status != "completed" {
		t.Fatal(job)
	}
	if err := api.database.InvalidateCache(context.Background(), session, "test-resource", "default"); err != nil {
		t.Fatal(err)
	}
	second, err := api.resources.Submit(context.Background(), session, "test-resource", "default", "mutation", true)
	if err != nil || second["job_id"] == first["job_id"] {
		t.Fatal("invalidated resource reused completed job", second, err)
	}
	if job := waitTestJob(t, api.resources, session, second); job.Status != "completed" || calls.Load() != 2 {
		t.Fatal(job, calls.Load())
	}
}
