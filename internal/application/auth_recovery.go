package application

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type authRecoveryState struct {
	runtime           *Runtime
	status, errorCode string
	jobID             string
	active            bool
	probing           bool
	failures          int
	next              time.Time
	nextProbe         time.Time
}

type savedCampusSession struct {
	Account  string               `json:"account"`
	Password string               `json:"password"`
	Mode     string               `json:"mode"`
	Cookies  []campus.SavedCookie `json:"cookies"`
}

func (api *API) authRecoverySnapshot(session store.Session) map[string]any {
	runtime := api.accounts.Runtime(session)
	if runtime != nil {
		if _, scoped := runtime.Client.(*serviceOnlyClient); scoped {
			return map[string]any{"status": "manual_required", "phase": "primary_unavailable", "retry_after_seconds": 0, "failures": 0}
		}
	}
	api.recoveryMutex.Lock()
	defer api.recoveryMutex.Unlock()
	state := api.recoveries[session.Account]
	if state == nil || state.runtime != runtime {
		if runtime != nil && runtime.Context.Err() == nil {
			if active, _ := runtime.Client.Status(); !active {
				return map[string]any{"status": "recovering", "phase": "scheduled", "retry_after_seconds": 0, "failures": 0}
			}
		}
		return map[string]any{"status": "idle"}
	}
	result := map[string]any{"status": state.status, "error_code": state.errorCode, "failures": state.failures, "retry_after_seconds": 0}
	if state.probing {
		result["phase"] = "checking_session"
	}
	if runtime != nil && (state.status == "idle" || state.status == "authenticated") {
		if active, _ := runtime.Client.Status(); !active {
			result["status"], result["phase"] = "recovering", "scheduled"
		}
	}
	if state.status == "retry_wait" {
		result["retry_after_seconds"] = max(0, int(time.Until(state.next).Seconds()))
		result["next_retry_at"] = state.next.UTC().Format(time.RFC3339Nano)
	}
	return result
}

func (api *API) scheduleAuthRecovery(session store.Session) {
	runtime := api.accounts.Runtime(session)
	if runtime == nil || runtime.Context.Err() != nil {
		return
	}
	if _, scoped := runtime.Client.(*serviceOnlyClient); scoped {
		return
	}
	api.cleanupPendingRecovery(session, runtime)
	active, _ := runtime.Client.Status()
	api.recoveryMutex.Lock()
	state := api.recoveries[session.Account]
	if state == nil || state.runtime != runtime {
		state = &authRecoveryState{runtime: runtime, status: "idle", nextProbe: time.Now().Add(90 * time.Second)}
		api.recoveries[session.Account] = state
	}
	if active {
		if _, supported := runtime.Client.(interface{ ProbeSession(context.Context) error }); !supported || time.Now().Before(state.nextProbe) {
			api.recoveryMutex.Unlock()
			return
		}
	}
	if state.active || state.status == "interaction_required" || state.status == "manual_required" || state.status == "credentials_invalid" || time.Now().Before(state.next) {
		api.recoveryMutex.Unlock()
		return
	}
	if previous, exists := api.resources.Job(session, state.jobID); exists && (previous.Status == "running" || previous.Status == "queued") {
		api.recoveryMutex.Unlock()
		return
	}
	state.active, state.status = true, "recovering"
	state.probing = active
	if active {
		state.status = "authenticated"
	}
	identifier, err := api.resources.startDocumentWork(session, "authentication", "primary", func(ctx context.Context, current *Runtime) error {
		entered := false
		err := api.resources.documentWorkStep(ctx, session, func() error {
			entered = true
			if active {
				return api.probePrimary(ctx, session, runtime, state)
			}
			return api.recoverPrimary(ctx, session, runtime, state)
		})
		if !entered {
			api.finishRecovery(session, state, "retry_wait", err)
		}
		return err
	})
	state.jobID = identifier
	api.recoveryMutex.Unlock()
	if err != nil {
		api.finishRecovery(session, state, "retry_wait", err)
	}
}

func (api *API) endPendingRecovery(flow *browserVPNFlow) {
	if flow.original == nil || flow.target == "jwxk" || flow.target == "cxcy" {
		return
	}
	api.recoveryMutex.Lock()
	defer api.recoveryMutex.Unlock()
	if state := api.recoveries[flow.bound.Account]; state != nil && state.runtime == flow.original {
		state.status, state.active = "manual_required", false
	}
}

func (api *API) cleanupPendingRecovery(session store.Session, runtime *Runtime) {
	api.vpnMutex.Lock()
	flow := api.pendingVPN[session.Account]
	api.vpnMutex.Unlock()
	if flow == nil || !flow.mutex.TryLock() {
		return
	}
	defer flow.mutex.Unlock()
	expired := flow.context.Err() != nil || flow.original != runtime || flow.bound.Generation != session.Generation || time.Since(flow.created) > 15*time.Minute
	if expiry, ok := flow.state["expires_at"].(int64); ok {
		grace := int64(60)
		if flow.state["sms_verified"] == true {
			grace = 0
		}
		expired = expired || time.Now().Unix() >= expiry+grace
	}
	if !expired {
		return
	}
	flow.cancel()
	flow.password = ""
	if !flow.adopted {
		flow.client.Close()
	}
	api.vpnMutex.Lock()
	if api.pendingVPN[session.Account] == flow {
		delete(api.pendingVPN, session.Account)
	}
	api.vpnMutex.Unlock()
	api.endPendingRecovery(flow)
}

func (api *API) finishRecovery(session store.Session, state *authRecoveryState, status string, err error) {
	api.recoveryMutex.Lock()
	defer api.recoveryMutex.Unlock()
	if api.recoveries[session.Account] != state {
		return
	}
	state.active, state.status = false, status
	state.probing = false
	state.errorCode = ""
	if status == "authenticated" {
		state.failures = 0
		state.next = time.Time{}
		state.nextProbe = time.Now().Add(90 * time.Second)
	}
	if err != nil {
		state.errorCode = "UPSTREAM_UNAVAILABLE"
		var failure *campus.Error
		if errors.As(err, &failure) {
			state.errorCode = failure.Code
		}
	}
	if status == "retry_wait" {
		state.failures++
		state.next = time.Now().Add(min(120*time.Second, 5*time.Second*time.Duration(1<<min(state.failures-1, 5))))
	}
}

func recoveryErrorStatus(err error) string {
	var failure *campus.Error
	if errors.As(err, &failure) {
		switch failure.Code {
		case "WRONG_PASSWORD", "INVALID_PASSWORD":
			return "credentials_invalid"
		case "AUTH_ACCOUNT_MISMATCH", "WEBVPN_ACCOUNT_MISMATCH", "WEBVPN_ACCOUNT_UNVERIFIED":
			return "manual_required"
		}
	}
	return "retry_wait"
}

func (api *API) newPendingVPNRecovery(session store.Session, original *Runtime, vpn VPNClient, target, password string, result map[string]any) (*browserVPNFlow, error) {
	identifier, _ := result["flow_id"].(string)
	if identifier == "" {
		return nil, errors.New("missing recovery flow")
	}
	parent := original.Context
	if target == "primary" {
		parent = api.accounts.context
	} else {
		password = ""
	}
	flowContext, cancel := context.WithCancel(parent)
	created := time.Now()
	flow := &browserVPNFlow{client: vpn, context: flowContext, cancel: cancel, created: created, identifier: identifier, password: password, target: target, remember: target == "primary", bound: session, original: original, state: map[string]any{}}
	for _, key := range []string{"status", "flow_id", "expires_in", "captcha_image", "sms_verified"} {
		if value, ok := result[key]; ok {
			flow.state[key] = value
		}
	}
	if seconds, ok := result["expires_in"].(int); ok {
		flow.state["expires_at"] = created.Add(time.Duration(seconds) * time.Second).Unix()
	}
	return flow, nil
}

func (api *API) recoverPrimary(ctx context.Context, session store.Session, original *Runtime, state *authRecoveryState) (recoveryError error) {
	status := "retry_wait"
	defer func() { api.finishRecovery(session, state, status, recoveryError) }()
	if api.accounts.Runtime(session) != original || ctx.Err() != nil {
		return store.ErrAccess
	}
	payload, err := api.database.ReadSecret(ctx, session.Account, "campus-session")
	remember := err == nil
	var saved savedCampusSession
	if errors.Is(err, store.ErrMissing) {
		cookies, mode := original.Client.Snapshot()
		if len(cookies) == 0 {
			status = "manual_required"
			return nil
		}
		saved = savedCampusSession{Account: session.Account, Cookies: cookies, Mode: mode}
	} else if err != nil {
		return err
	} else if json.Unmarshal(payload, &saved) != nil {
		status = "manual_required"
		return store.ErrConflict
	}
	if saved.Account != session.Account || (saved.Mode != "direct" && saved.Mode != "webvpn") {
		status = "manual_required"
		return store.ErrConflict
	}
	candidate, err := api.accounts.factory(session.Account)
	if err != nil {
		return err
	}
	retained := false
	defer func() {
		if !retained {
			candidate.Close()
		}
	}()
	if restorer, ok := candidate.(interface {
		Restore([]campus.SavedCookie, string)
	}); ok {
		restorer.Restore(saved.Cookies, saved.Mode)
	}
	verified := false
	if verifier, ok := candidate.(interface{ VerifySaved(context.Context) error }); ok {
		err = verifier.VerifySaved(ctx)
		verified = err == nil
	}
	if !verified {
		var probeFailure *campus.Error
		if errors.As(err, &probeFailure) {
			switch probeFailure.Code {
			case "UPSTREAM_UNAVAILABLE", "WEBVPN_UPSTREAM_TIMEOUT", "UPSTREAM_RESPONSE_INVALID":
				return err
			case "AUTH_ACCOUNT_MISMATCH", "WEBVPN_ACCOUNT_MISMATCH", "WEBVPN_ACCOUNT_UNVERIFIED":
				status = "manual_required"
				return err
			}
		}
		if saved.Password == "" {
			status = "manual_required"
			return err
		}
		candidate.Close()
		var result map[string]any
		if saved.Mode == "direct" {
			err = candidate.LoginDirect(ctx, saved.Password)
			var failure *campus.Error
			if errors.As(err, &failure) && (failure.Code == "DIRECT_ACCESS_FAILED" || failure.Code == "WEBVPN_REQUIRED") {
				if vpn, ok := candidate.(VPNClient); ok {
					result, err = vpn.StartVPNPassword(ctx, saved.Password)
				}
			}
		} else if vpn, ok := candidate.(VPNClient); ok {
			result, err = vpn.StartVPNPassword(ctx, saved.Password)
			var failure *campus.Error
			if errors.As(err, &failure) && failure.Code == "WEBVPN_CAMPUS_NETWORK" {
				candidate.Close()
				err = candidate.LoginDirect(ctx, saved.Password)
				result = nil
			}
		} else {
			status = "manual_required"
			return errors.New("webvpn recovery unavailable")
		}
		if result["status"] == "sms_required" || result["status"] == "session_pending" {
			vpn, ok := candidate.(VPNClient)
			if !ok || ctx.Err() != nil || api.accounts.Runtime(session) != original {
				return store.ErrAccess
			}
			flow, err := api.newPendingVPNRecovery(session, original, vpn, "primary", saved.Password, result)
			if err != nil {
				return err
			}
			api.vpnMutex.Lock()
			if old := api.pendingVPN[session.Account]; old != nil {
				old.cancel()
			}
			api.pendingVPN[session.Account] = flow
			api.vpnMutex.Unlock()
			retained, status = true, "interaction_required"
			return nil
		}
		if err != nil {
			status = recoveryErrorStatus(err)
			return err
		}
		if active, _ := candidate.Status(); !active {
			return errors.New("recovery did not authenticate")
		}
	}
	lockValue, _ := api.accounts.loginLocks.LoadOrStore(session.Account, &sync.Mutex{})
	lock := lockValue.(*sync.Mutex)
	lock.Lock()
	if api.accounts.Runtime(session) != original || ctx.Err() != nil {
		lock.Unlock()
		return store.ErrAccess
	}
	err = api.accounts.installClient(ctx, candidate, saved.Password, remember, session.Generation)
	lock.Unlock()
	if err != nil {
		return err
	}
	retained, status = true, "authenticated"
	current := api.accounts.Runtime(session)
	api.recoveryMutex.Lock()
	state.runtime = current
	api.recoveryMutex.Unlock()
	api.resumeAccountWork(session)
	return nil
}
