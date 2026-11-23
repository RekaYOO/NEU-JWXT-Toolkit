package application

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) recoverVPNService(ctx context.Context, session store.Session, original *Runtime, target, password string) error {
	if target != "jwxk" && target != "cxcy" || api.accounts.Runtime(session) != original || ctx.Err() != nil {
		return store.ErrAccess
	}
	api.cleanupPendingRecovery(session, original)
	client, err := api.vpnFactory(session.Account)
	if err != nil {
		return err
	}
	selector, ok := client.(interface{ SetVPNTarget(string) error })
	if !ok {
		client.Close()
		return store.ErrAccess
	}
	if err := selector.SetVPNTarget(target); err != nil {
		client.Close()
		return err
	}
	flowContext, cancel := context.WithCancel(original.Context)
	flow := &browserVPNFlow{client: client, context: flowContext, cancel: cancel, created: time.Now(), target: target, bound: session, original: original, state: map[string]any{}}
	flow.mutex.Lock()
	defer flow.mutex.Unlock()
	api.vpnMutex.Lock()
	busy := api.pendingVPN[session.Account] != nil
	for _, existing := range api.vpnFlows {
		if existing.registeredAccount == session.Account && existing.context.Err() == nil && time.Since(existing.created) < 15*time.Minute {
			busy = true
		}
	}
	if !busy {
		api.pendingVPN[session.Account] = flow
	}
	api.vpnMutex.Unlock()
	retained := false
	defer func() {
		if !retained {
			cancel()
			api.vpnMutex.Lock()
			if api.pendingVPN[session.Account] == flow {
				delete(api.pendingVPN, session.Account)
			}
			api.vpnMutex.Unlock()
			if !flow.adopted {
				client.Close()
			}
		}
	}()
	if busy {
		return &campus.Error{Code: "SERVICE_AUTH_REQUIRED", Message: "请先完成已有验证"}
	}
	call, stop := context.WithCancel(ctx)
	defer stop()
	detach := context.AfterFunc(flowContext, stop)
	defer detach()
	result, err := client.StartVPNPassword(call, password)
	if call.Err() != nil || api.accounts.Runtime(session) != original {
		return store.ErrAccess
	}
	switch result["status"] {
	case "sms_required", "session_pending":
		flow.identifier, _ = result["flow_id"].(string)
		if flow.identifier == "" {
			return store.ErrConflict
		}
		for _, key := range []string{"status", "flow_id", "expires_in", "captcha_image", "sms_verified"} {
			if value, ok := result[key]; ok {
				flow.state[key] = value
			}
		}
		if seconds, ok := result["expires_in"].(int); ok {
			flow.state["expires_at"] = time.Now().Add(time.Duration(seconds) * time.Second).Unix()
		}
		retained = true
		return &campus.Error{Code: "SERVICE_INTERACTION_REQUIRED", Message: "请继续当前子服务验证"}
	case "authenticated":
		if err == nil {
			return api.adoptVPN(ctx, flow)
		}
	}
	if err != nil {
		return err
	}
	return store.ErrAccess
}

func (api *API) adoptVPNService(ctx context.Context, flow *browserVPNFlow, session store.Session) error {
	api.accounts.mutex.Lock()
	defer api.accounts.mutex.Unlock()
	runtime := api.accounts.runtimes[session.Account]
	if runtime != nil && (runtime.Generation != session.Generation || runtime.Context.Err() != nil) {
		return store.ErrAccess
	}
	var scoped *serviceOnlyClient
	if runtime != nil {
		scoped, _ = runtime.Client.(*serviceOnlyClient)
	}
	if (runtime == nil || scoped != nil) && flow.target == "jwxk" {
		cookies, _ := flow.client.Snapshot()
		payload, err := json.Marshal(savedServiceSession{Account: session.Account, Generation: session.Generation, Cookies: cookies})
		if err != nil {
			return err
		}
		if err := api.database.SaveSecretForSession(ctx, session, "campus-service-jwxk", payload); err != nil {
			return err
		}
		if err := api.database.InvalidateRecoveryContexts(ctx, session, "jwxk"); err != nil {
			return err
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if scoped != nil {
			scoped.mutex.Lock()
			previous := scoped.candidate
			scoped.candidate = flow.client
			scoped.mutex.Unlock()
			previous.Close()
		} else {
			runtimeContext, cancel := context.WithCancel(api.accounts.context)
			api.accounts.runtimes[session.Account] = &Runtime{Client: &serviceOnlyClient{candidate: flow.client}, Context: runtimeContext, Cancel: cancel, Generation: session.Generation}
		}
		flow.adopted = true
		return nil
	}
	if runtime == nil || runtime.Generation != session.Generation || runtime.Context.Err() != nil {
		return store.ErrAccess
	}
	merger, ok := runtime.Client.(interface {
		AdoptVPNGateway(string, []campus.SavedCookie) error
	})
	if !ok {
		return errors.New("gateway merge unavailable")
	}
	cookies, _ := flow.client.Snapshot()
	if err := merger.AdoptVPNGateway(session.Account, cookies); err != nil {
		return err
	}
	if saved, err := api.database.ReadSecret(ctx, session.Account, "campus-session"); err == nil {
		var state map[string]any
		if json.Unmarshal(saved, &state) != nil {
			return store.ErrConflict
		}
		cookies, mode := runtime.Client.Snapshot()
		state["cookies"], state["mode"] = cookies, mode
		payload, _ := json.Marshal(state)
		if err := api.database.SaveSecretForSession(ctx, session, "campus-session", payload); err != nil {
			return err
		}
	} else if !errors.Is(err, store.ErrMissing) {
		return err
	}
	flow.client.Close()
	return api.database.InvalidateRecoveryContexts(ctx, session, flow.target)
}
