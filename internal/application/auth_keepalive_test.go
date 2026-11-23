package application

import (
	"context"
	"encoding/json"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type keepaliveTestClient struct {
	testCampusClient
	checks atomic.Int32
	expire bool
	fail   bool
}

func (client *keepaliveTestClient) ProbeSession(context.Context) error {
	client.checks.Add(1)
	if client.expire {
		client.active.Store(false)
		return &campus.Error{Code: "AUTH_REQUIRED", Message: "expired"}
	}
	if client.fail {
		return &campus.Error{Code: "UPSTREAM_UNAVAILABLE", Message: "temporary"}
	}
	return nil
}
func (client *keepaliveTestClient) Snapshot() ([]campus.SavedCookie, string) {
	return []campus.SavedCookie{{URL: "https://jwxt.neu.edu.cn/", Cookie: http.Cookie{Name: "rotated", Value: "test-cookie", Path: "/"}}}, "direct"
}

func TestKeepaliveIntervalTemporaryFailureAndSharedRecovery(t *testing.T) {
	api, handler, cookie, session, original := setupAuthRecovery(t, "direct", "saved-password")
	client := &keepaliveTestClient{testCampusClient: testCampusClient{account: "student"}}
	client.active.Store(true)
	original.Client = client
	api.scheduleAuthRecovery(session)
	if client.checks.Load() != 0 {
		t.Fatal("new runtime probed immediately")
	}
	forceProbe := func() {
		api.recoveryMutex.Lock()
		api.recoveries[session.Account].nextProbe = time.Now().Add(-time.Second)
		api.recoveryMutex.Unlock()
		api.scheduleAuthRecovery(session)
		waitRecovery(t, api, session)
	}
	forceProbe()
	if client.checks.Load() != 1 || api.accounts.Runtime(session) != original {
		t.Fatal("keepalive replaced live runtime")
	}
	payload, err := api.database.ReadSecret(context.Background(), session.Account, "campus-session")
	var saved savedCampusSession
	if err != nil || json.Unmarshal(payload, &saved) != nil || len(saved.Cookies) != 1 || saved.Cookies[0].Cookie.Name != "rotated" || saved.Password != "saved-password" {
		t.Fatal("cookie refresh lost credentials", err)
	}
	apiRequest(handler, "GET", "/api/status", "", cookie)
	api.scheduleAuthRecovery(session)
	if client.checks.Load() != 1 {
		t.Fatal("status or early tick triggered probe")
	}
	client.fail = true
	forceProbe()
	if !client.active.Load() || api.accounts.Runtime(session) != original {
		t.Fatal("temporary failure invalidated session")
	}
	api.recoveryMutex.Lock()
	next := api.recoveries[session.Account].nextProbe
	api.recoveryMutex.Unlock()
	if time.Until(next) < 89*time.Second {
		t.Fatal("probe interval not maintained")
	}
	client.fail = false
	client.expire = true
	forceProbe()
	if client.active.Load() {
		t.Fatal("expired session remained active")
	}
	candidate := &recoveryTestClient{testVPNClient: testVPNClient{testCampusClient: testCampusClient{account: "student"}}, probe: func(context.Context) error { return nil }}
	api.accounts.factory = func(string) (CampusClient, error) { return candidate, nil }
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	if api.accounts.Runtime(session) == original || candidate.probes.Load() != 1 || candidate.directs.Load() != 0 {
		t.Fatal("expiry did not enter shared cookie recovery")
	}
}

func TestKeepaliveDoesNotPersistUnrememberedCookies(t *testing.T) {
	api, _, _, session, original := setupAuthRecovery(t, "direct", "")
	api.database.DeleteSecret(context.Background(), session.Account, "campus-session")
	client := &keepaliveTestClient{testCampusClient: testCampusClient{account: "student"}}
	client.active.Store(true)
	original.Client = client
	api.scheduleAuthRecovery(session)
	api.recoveryMutex.Lock()
	api.recoveries[session.Account].nextProbe = time.Now().Add(-time.Second)
	api.recoveryMutex.Unlock()
	api.scheduleAuthRecovery(session)
	waitRecovery(t, api, session)
	if client.checks.Load() != 1 {
		t.Fatal("probe missing")
	}
	if _, err := api.database.ReadSecret(context.Background(), session.Account, "campus-session"); err == nil {
		t.Fatal("keepalive persisted unremembered login")
	}
}
