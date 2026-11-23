package application

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type festivalTestClient struct {
	testCampusClient
	calls   atomic.Int32
	failure bool
	mode    string
}

func (client *festivalTestClient) RequestService(_ context.Context, input campus.ServiceRequest) ([]byte, error) {
	client.calls.Add(1)
	if client.mode != "" && input.Mode != client.mode {
		return nil, errors.New("route changed during scan")
	}
	if client.failure {
		return nil, &campus.Error{Code: "SERVICE_AUTH_REQUIRED", Message: "synthetic expired"}
	}
	if input.Service != "cxcy" {
		return nil, errors.New("wrong service")
	}
	if strings.Contains(input.Path, "/comp/ucenter/") {
		if strings.HasPrefix(input.Path, "/popscience/") {
			return []byte(`<div class="list_item">活动名称：测试活动<a href="../../front/comp/info?id=1">team</a><img src="/static/uploads/res/popsciencecert/a.png"></div>`), nil
		}
		return []byte(`<html>暂无记录</html>`), nil
	}
	return []byte(`<h1>测试活动</h1><div>活动时间：2026-11-16 10:00</div>`), nil
}

func TestFestivalOnlineCacheAndSettingsKeepPrivateURLsOffWire(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	client := &festivalTestClient{testCampusClient: testCampusClient{account: "student"}, mode: "webvpn"}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	response := apiRequest(handler, "PUT", "/api/export/festival-activities/settings?probe=false", `{"network_mode":"webvpn"}`, cookie)
	if response.Code != 200 || client.calls.Load() != 0 {
		t.Fatal(response.Code, response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/export/festival-activities", "", cookie)
	if response.Code != 200 || strings.Contains(response.Body.String(), "certificate_url") || strings.Contains(response.Body.String(), "detail_url") || !strings.Contains(response.Body.String(), `"certificate_available":true`) {
		t.Fatal(response.Code, response.Body.String())
	}
	if _, err := api.database.ReadCache(context.Background(), session, "festival-activities", "default"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("on-demand persisted cache", err)
	}
	job, err := api.resources.Submit(context.Background(), session, "festival-activities", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	if result := waitTestJob(t, api.resources, session, job); result.Status != "completed" || result.AuthScope != "cxcy" {
		t.Fatal(result)
	}
	entry, err := api.database.ReadCache(context.Background(), session, "festival-activities", "default")
	if err != nil || !strings.Contains(string(entry.Payload), "certificate_url") {
		t.Fatal("private cache missing controlled reference", err)
	}
	response = apiRequest(handler, "GET", "/api/export/festival-activities/cache", "", cookie)
	if response.Code != 200 || strings.Contains(response.Body.String(), "static/uploads") {
		t.Fatal("private URL leaked", response.Body.String())
	}
	before := client.calls.Load()
	apiRequest(handler, "POST", "/api/logout", "", cookie)
	response = apiRequest(handler, "GET", "/api/offline/festival-activities", "", nil)
	if response.Code != 200 || client.calls.Load() != before || !strings.Contains(response.Body.String(), `"source":"offline"`) {
		t.Fatal("offline requested school", response.Code)
	}
}

func TestFestivalPendingChallengeIsServiceScoped(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	client := &festivalTestClient{testCampusClient: testCampusClient{account: "student"}, mode: "webvpn"}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student")
	session, err := api.database.Session(context.Background(), cookie.Value)
	if err != nil {
		t.Fatal(err)
	}
	apiRequest(handler, "PUT", "/api/export/festival-activities/settings?probe=false", `{"network_mode":"webvpn"}`, cookie)
	for _, target := range []string{"cxcy", "jwxk"} {
		api.pendingVPN[session.Account] = &browserVPNFlow{bound: session, target: target, context: context.Background(), created: time.Now(), state: map[string]any{"status": "sms_required", "captcha_image": "fixture-private"}}
		response := apiRequest(handler, "GET", "/api/export/festival-activities/status", "", cookie)
		delete(api.pendingVPN, session.Account)
		if response.Code != 200 || strings.Contains(response.Body.String(), "fixture-private") || strings.Contains(response.Body.String(), "interaction_required") != (target == "cxcy") || (client.calls.Load() == 0) != (target == "cxcy") {
			t.Fatal(target, response.Body.String(), client.calls.Load())
		}
	}
}

func TestFestivalAuthFailureStaysInServiceScope(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	client := &festivalTestClient{testCampusClient: testCampusClient{account: "student"}, failure: true}
	api.accounts.factory = func(string) (CampusClient, error) { return client, nil }
	cookie := loginCookie(t, handler, "student")
	response := apiRequest(handler, "GET", "/api/export/festival-activities", "", cookie)
	if response.Code != 401 || !strings.Contains(response.Body.String(), `"auth_scope":"cxcy"`) || !strings.Contains(response.Body.String(), "CXCY_LOGIN_REQUIRED") {
		t.Fatal(response.Code, response.Body.String())
	}
	if active, _ := client.Status(); !active {
		t.Fatal("child failure logged out primary")
	}
}

func TestFestivalDeletionWaitsForOldRefreshAndOnlyDeletesExactKey(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	for _, item := range []struct{ resource, variant string }{{"festival-activities", "other"}, {"scores", "default"}} {
		if _, err := api.database.CommitCache(context.Background(), session, store.CacheEntry{Resource: item.resource, Variant: item.variant, SchemaVersion: 1, Payload: json.RawMessage(`{}`), Revision: "preserved"}, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	started, release := make(chan struct{}), make(chan struct{})
	api.resources.Register("festival-activities", ResourceSpec{Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		close(started)
		<-release
		return json.RawMessage(`{"activities":[],"warnings":[]}`), nil, nil
	}})
	job, err := api.resources.Submit(context.Background(), session, "festival-activities", "default", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	<-started
	done := make(chan int, 1)
	go func() {
		done <- apiRequest(handler, "DELETE", "/api/export/festival-activities/cache", "", cookie).Code
	}()
	close(release)
	if status := <-done; status != 200 {
		t.Fatal(status)
	}
	waitTestJob(t, api.resources, session, job)
	if _, err := api.database.ReadCache(context.Background(), session, "festival-activities", "default"); !errors.Is(err, store.ErrMissing) {
		t.Fatal("old refresh resurrected cache", err)
	}
	for _, item := range []struct{ resource, variant string }{{"festival-activities", "other"}, {"scores", "default"}} {
		if _, err := api.database.ReadCache(context.Background(), session, item.resource, item.variant); err != nil {
			t.Fatal("deleted unrelated cache", err)
		}
	}
	api.resources.Register("festival-activities", ResourceSpec{Fetch: func(context.Context, *Runtime, string) (json.RawMessage, json.RawMessage, error) {
		return json.RawMessage(`{"activities":[],"warnings":[]}`), nil, nil
	}})
	next, err := api.resources.Submit(context.Background(), session, "festival-activities", "default", "manual", true)
	if err != nil || next["job_id"] == job["job_id"] {
		t.Fatal("deleted job reused", next, err)
	}
	if result := waitTestJob(t, api.resources, session, next); result.Status != "completed" {
		t.Fatal(result)
	}
}

func TestFestivalDeletionRejectsRevokedIdentityAndKeepsOtherAccounts(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student", "other-student"} {
		if err := api.database.AddAccount(context.Background(), account); err != nil {
			t.Fatal(err)
		}
	}
	cookie := loginCookie(t, handler, "student")
	session, _ := api.database.Session(context.Background(), cookie.Value)
	otherCookie := loginCookie(t, handler, "other-student")
	other, _ := api.database.Session(context.Background(), otherCookie.Value)
	for _, identity := range []store.Session{session, other} {
		if _, err := api.database.CommitCache(context.Background(), identity, store.CacheEntry{Resource: "festival-activities", Variant: "default", SchemaVersion: 1, Payload: json.RawMessage(`{"activities":[],"warnings":[]}`), Revision: "saved"}, "test", nil); err != nil {
			t.Fatal(err)
		}
	}
	if _, _, err := api.resources.Delete(context.Background(), session, "festival-activities", "default"); err != nil {
		t.Fatal(err)
	}
	if _, err := api.database.ReadCache(context.Background(), other, "festival-activities", "default"); err != nil {
		t.Fatal("other account erased", err)
	}
	if err := api.database.RevokeAccount(context.Background(), other.Account); err != nil {
		t.Fatal(err)
	}
	if _, _, err := api.resources.Delete(context.Background(), other, "festival-activities", "default"); !errors.Is(err, store.ErrAccess) {
		t.Fatal("revoked deletion accepted", err)
	}
}
