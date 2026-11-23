package application

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (resources *Resources) startDocumentWork(session store.Session, name, variant string, work func(context.Context, *Runtime) error) (string, error) {
	resources.accounts.mutex.Lock()
	defer resources.accounts.mutex.Unlock()
	runtime := resources.accounts.runtimes[session.Account]
	if resources.accounts.context.Err() != nil || runtime == nil || runtime.Generation != session.Generation || runtime.Context.Err() != nil {
		return "", store.ErrAccess
	}
	key := session.Account + "\x00" + strconv.FormatInt(session.Generation, 10) + "\x00document:" + name + "\x00" + variant
	resources.mutex.Lock()
	defer resources.mutex.Unlock()
	if id := resources.active[key]; id != "" {
		return id, nil
	}
	for id, job := range resources.jobs {
		if job.Status != "running" && job.Status != "queued" && time.Since(job.UpdatedAt) > time.Hour {
			delete(resources.jobs, id)
		}
	}
	if len(resources.active) >= 256 {
		return "", &campus.Error{Code: "BUSY", Message: "后台任务繁忙，请稍后刷新"}
	}
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return "", err
	}
	job := &CacheJob{ID: hex.EncodeToString(identifier), Resource: name, Variant: variant, Status: "queued", Reason: "document_sync", CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(), account: session.Account, generation: session.Generation, done: make(chan struct{}), Diff: map[string]any{}}
	resources.jobs[job.ID], resources.active[key] = job, job.ID
	resources.accounts.workers.Add(1)
	go func() {
		defer resources.accounts.workers.Done()
		ctx, cancel := context.WithTimeout(runtime.Context, 15*time.Minute)
		defer cancel()
		resources.mutex.Lock()
		job.Status, job.UpdatedAt = "running", time.Now().UTC()
		resources.mutex.Unlock()
		err := work(ctx, runtime)
		resources.mutex.Lock()
		defer resources.mutex.Unlock()
		job.Status, job.UpdatedAt = "completed", time.Now().UTC()
		if err != nil {
			job.Status, job.ErrorKind = "failed", "UPSTREAM_RESPONSE_INVALID"
			var failure *campus.Error
			if errors.As(err, &failure) {
				job.ErrorKind = failure.Code
			}
			if runtime.Context.Err() != nil || errors.Is(err, store.ErrAccess) || errors.Is(err, store.ErrMissing) {
				job.Status, job.ErrorKind = "cancelled", "identity_or_document_changed"
			}
		}
		delete(resources.active, key)
		close(job.done)
	}()
	return job.ID, nil
}

func (resources *Resources) documentWorkStep(ctx context.Context, session store.Session, work func() error) error {
	release, err := resources.acquireAccount(ctx, session)
	if err != nil {
		return err
	}
	defer release()
	select {
	case resources.slots <- struct{}{}:
		defer func() { <-resources.slots }()
	case <-ctx.Done():
		return ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return work()
}
