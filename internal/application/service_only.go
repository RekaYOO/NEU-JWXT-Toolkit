package application

import (
	"context"
	"encoding/json"
	"net/url"
	"sync"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type serviceOnlyClient struct {
	mutex     sync.Mutex
	candidate CampusClient
}

func (client *serviceOnlyClient) Account() string {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	return client.candidate.Account()
}
func (client *serviceOnlyClient) Status() (bool, string)                   { return false, "webvpn" }
func (client *serviceOnlyClient) Snapshot() ([]campus.SavedCookie, string) { return nil, "webvpn" }
func (client *serviceOnlyClient) Close() {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	client.candidate.Close()
}
func (client *serviceOnlyClient) gatewaySnapshot() []campus.SavedCookie {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	cookies, _ := client.candidate.Snapshot()
	return cookies
}
func (client *serviceOnlyClient) LoginDirect(context.Context, string) error { return store.ErrAccess }
func (client *serviceOnlyClient) Request(context.Context, string, string, url.Values) ([]byte, error) {
	return nil, &campus.Error{Code: "AUTH_EXPIRED", Message: "主教务会话尚未恢复"}
}
func (client *serviceOnlyClient) TaskServiceAvailable(service string) bool { return service == "jwxk" }
func (client *serviceOnlyClient) RequestService(ctx context.Context, request campus.ServiceRequest) ([]byte, error) {
	client.mutex.Lock()
	defer client.mutex.Unlock()
	if request.Service != "jwxk" || request.Mode != "" && request.Mode != "inherit" && request.Mode != "webvpn" {
		return nil, &campus.Error{Code: "SERVICE_NOT_SENT", Message: "独立会话仅允许WebVPN选课业务，请恢复相应服务登录"}
	}
	if active, _ := client.candidate.Status(); !active {
		verifier, ok := client.candidate.(interface {
			VerifyServiceSession(context.Context, string, string) error
		})
		if !ok {
			return nil, store.ErrAccess
		}
		if err := verifier.VerifyServiceSession(ctx, "jwxk", "webvpn"); err != nil {
			return nil, err
		}
	}
	service, ok := client.candidate.(interface {
		RequestService(context.Context, campus.ServiceRequest) ([]byte, error)
	})
	if !ok {
		return nil, store.ErrAccess
	}
	request.Mode = "webvpn"
	return service.RequestService(ctx, request)
}

type savedServiceSession struct {
	Account    string               `json:"account"`
	Generation int64                `json:"generation"`
	Cookies    []campus.SavedCookie `json:"cookies"`
}

func (accounts *Accounts) restoreServiceOnly() error {
	sessions, err := accounts.database.ServiceSessionAccounts(accounts.context)
	if err != nil {
		return err
	}
	for _, session := range sessions {
		if accounts.Runtime(session) != nil {
			continue
		}
		payload, err := accounts.database.ReadSecret(accounts.context, session.Account, "campus-service-jwxk")
		if err != nil {
			continue
		}
		var saved savedServiceSession
		if json.Unmarshal(payload, &saved) != nil || saved.Account != session.Account || saved.Generation != session.Generation {
			continue
		}
		candidate, err := accounts.factory(session.Account)
		if err != nil {
			continue
		}
		restorer, ok := candidate.(interface {
			Restore([]campus.SavedCookie, string)
		})
		if !ok {
			candidate.Close()
			continue
		}
		restorer.Restore(saved.Cookies, "webvpn")
		ctx, cancel := context.WithCancel(accounts.context)
		accounts.mutex.Lock()
		generation, err := accounts.database.Generation(accounts.context, session.Account)
		if accounts.context.Err() == nil && err == nil && generation == session.Generation && accounts.runtimes[session.Account] == nil {
			accounts.runtimes[session.Account] = &Runtime{Client: &serviceOnlyClient{candidate: candidate}, Context: ctx, Cancel: cancel, Generation: session.Generation}
		} else {
			cancel()
			candidate.Close()
		}
		accounts.mutex.Unlock()
	}
	return nil
}
