package application

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) probePrimary(ctx context.Context, session store.Session, runtime *Runtime, state *authRecoveryState) (probeError error) {
	status := "authenticated"
	defer func() {
		api.finishRecovery(session, state, status, probeError)
		api.recoveryMutex.Lock()
		if api.recoveries[session.Account] == state {
			state.nextProbe = time.Now().Add(90 * time.Second)
		}
		api.recoveryMutex.Unlock()
	}()
	if api.accounts.Runtime(session) != runtime || ctx.Err() != nil {
		return store.ErrAccess
	}
	probe, ok := runtime.Client.(interface{ ProbeSession(context.Context) error })
	if !ok {
		return nil
	}
	if err := probe.ProbeSession(ctx); err != nil {
		if active, _ := runtime.Client.Status(); !active {
			status = "idle"
			if recoveryErrorStatus(err) == "manual_required" {
				status = "manual_required"
			}
		}
		return err
	}
	lockValue, _ := api.accounts.loginLocks.LoadOrStore(session.Account, &sync.Mutex{})
	lock := lockValue.(*sync.Mutex)
	lock.Lock()
	defer lock.Unlock()
	if api.accounts.Runtime(session) != runtime || ctx.Err() != nil {
		return store.ErrAccess
	}
	payload, err := api.database.ReadSecret(ctx, session.Account, "campus-session")
	if errors.Is(err, store.ErrMissing) {
		return nil
	}
	if err != nil {
		return err
	}
	var saved savedCampusSession
	if json.Unmarshal(payload, &saved) != nil || saved.Account != session.Account {
		return store.ErrConflict
	}
	saved.Cookies, saved.Mode = runtime.Client.Snapshot()
	payload, err = json.Marshal(saved)
	if err != nil {
		return err
	}
	return api.database.SaveSecretForSession(ctx, session, "campus-session", payload)
}
