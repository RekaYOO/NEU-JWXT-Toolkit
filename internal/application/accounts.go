package application

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"sync"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type CampusClient interface {
	Account() string
	Status() (bool, string)
	LoginDirect(context.Context, string) error
	Close()
	Snapshot() ([]campus.SavedCookie, string)
	Request(context.Context, string, string, url.Values) ([]byte, error)
}

type Runtime struct {
	accounts          *Accounts
	recoveryMutex     sync.Mutex
	serviceRetryAfter map[string]time.Time
	Client            CampusClient
	Context           context.Context
	Cancel            context.CancelFunc
	Generation        int64
}

type Accounts struct {
	recoverVPNService func(context.Context, store.Session, *Runtime, string, string) error
	mutex             sync.RWMutex
	loginLocks        sync.Map
	database          *store.Store
	runtimes          map[string]*Runtime
	factory           func(string) (CampusClient, error)
	mode              string
	context           context.Context
	cancel            context.CancelFunc
	workers           sync.WaitGroup
	onReady           func(store.Session)
	automationOnce    sync.Once
	automationTick    func()
	mailOnce          sync.Once
	mailTick          func()
	mailWake          chan struct{}
	mailActive        map[string]bool
	mailSlots         chan struct{}
}

func NewAccounts(database *store.Store, mode string) (*Accounts, error) {
	if mode != "local-single" && mode != "remote-single" && mode != "remote-multi" {
		return nil, errors.New("invalid deployment mode")
	}
	ctx := context.Background()
	if err := database.InitializeDeploymentMode(ctx, mode); err != nil {
		return nil, err
	}
	saved, err := database.SiteSetting(ctx, "deployment-mode")
	if errors.Is(err, store.ErrMissing) {
		return nil, errors.New("data directory contains multiple accounts; single-user initialization requires an explicit data migration")
	}
	if err != nil {
		return nil, err
	}
	if string(saved) != mode {
		return nil, errors.New("deployment mode differs from stored data; use an explicit data migration")
	}
	rootContext, cancel := context.WithCancel(context.Background())
	return &Accounts{database: database, mode: mode, context: rootContext, cancel: cancel, runtimes: map[string]*Runtime{}, factory: func(account string) (CampusClient, error) { return campus.NewClient(account) }}, nil
}

func (accounts *Accounts) Login(ctx context.Context, account, password string, remember bool) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(accounts.context, cancel)
	defer stop()
	normalized, err := campus.NormalizeAccount(account)
	if err != nil {
		return err
	}
	if accounts.mode == "local-single" {
		if err := accounts.database.AddAccount(ctx, normalized); err != nil {
			return err
		}
	}
	generation, err := accounts.database.Generation(ctx, normalized)
	if err != nil {
		return err
	}
	lockValue, _ := accounts.loginLocks.LoadOrStore(normalized, &sync.Mutex{})
	loginLock := lockValue.(*sync.Mutex)
	loginLock.Lock()
	defer loginLock.Unlock()
	client, err := accounts.factory(normalized)
	if err != nil {
		return err
	}
	if err := client.LoginDirect(ctx, password); err != nil {
		client.Close()
		return err
	}
	return accounts.installClient(ctx, client, password, remember, generation)
}

func (accounts *Accounts) installClient(ctx context.Context, client CampusClient, password string, remember bool, generation int64) error {
	normalized := client.Account()
	accounts.mutex.Lock()
	defer accounts.mutex.Unlock()
	currentGeneration, err := accounts.database.Generation(ctx, normalized)
	if err != nil || currentGeneration != generation {
		client.Close()
		return store.ErrAccess
	}
	if previous := accounts.runtimes[normalized]; previous != nil {
		if scoped, ok := previous.Client.(*serviceOnlyClient); ok {
			if merger, ok := client.(interface {
				AdoptVPNGateway(string, []campus.SavedCookie) error
			}); ok {
				cookies := scoped.gatewaySnapshot()
				if err := merger.AdoptVPNGateway(normalized, cookies); err != nil {
					client.Close()
					return err
				}
			}
		}
	}
	if remember {
		cookies, mode := client.Snapshot()
		payload, err := json.Marshal(map[string]any{"account": normalized, "password": password, "cookies": cookies, "mode": mode})
		if err != nil {
			client.Close()
			return err
		}
		if err := accounts.database.SaveSecretForSession(ctx, store.Session{Account: normalized, Generation: generation}, "campus-session", payload); err != nil {
			client.Close()
			return err
		}
	} else {
		if err := accounts.database.DeleteSecret(ctx, normalized, "campus-session"); err != nil {
			client.Close()
			return err
		}
	}
	if err := accounts.database.InvalidateRecoveryContexts(ctx, store.Session{Account: normalized, Generation: generation}, "primary"); err != nil {
		client.Close()
		return err
	}
	if err := ctx.Err(); err != nil {
		client.Close()
		return err
	}
	for existing, runtime := range accounts.runtimes {
		if existing == normalized || accounts.mode != "remote-multi" {
			runtime.Cancel()
			if existing != normalized {
				if err := accounts.database.RevokeAccount(ctx, existing); err != nil {
					client.Close()
					return err
				}
			}
			go runtime.Client.Close()
			delete(accounts.runtimes, existing)
		}
	}
	runtimeContext, cancel := context.WithCancel(accounts.context)
	accounts.runtimes[normalized] = &Runtime{accounts: accounts, Client: client, Context: runtimeContext, Cancel: cancel, Generation: generation}
	return nil
}

func (accounts *Accounts) Runtime(session store.Session) *Runtime {
	accounts.mutex.RLock()
	defer accounts.mutex.RUnlock()
	runtime := accounts.runtimes[session.Account]
	if runtime == nil || runtime.Generation != session.Generation {
		return nil
	}
	return runtime
}

func (accounts *Accounts) CreateSession(ctx context.Context, account string, lifetime time.Duration) (string, error) {
	accounts.mutex.RLock()
	defer accounts.mutex.RUnlock()
	runtime := accounts.runtimes[account]
	if runtime == nil || runtime.Context.Err() != nil {
		return "", store.ErrAccess
	}
	if loggedIn, _ := runtime.Client.Status(); !loggedIn {
		return "", store.ErrAccess
	}
	return accounts.database.CreateSessionAtGeneration(ctx, account, runtime.Generation, lifetime)
}

func (accounts *Accounts) Stop(ctx context.Context, account string, forget bool) error {
	return accounts.stopSession(ctx, store.Session{Account: account}, forget, false)
}

func (accounts *Accounts) stopSession(ctx context.Context, session store.Session, forget, clearData bool) error {
	account := session.Account
	accounts.mutex.Lock()
	defer accounts.mutex.Unlock()
	if err := accounts.database.EndAccountSession(ctx, session, clearData, forget); err != nil {
		return err
	}
	if runtime := accounts.runtimes[account]; runtime != nil {
		runtime.Cancel()
		delete(accounts.runtimes, account)
		go runtime.Client.Close()
	}
	return nil
}

func (accounts *Accounts) Close() {
	accounts.cancel()
	accounts.mutex.Lock()
	for account, runtime := range accounts.runtimes {
		runtime.Cancel()
		runtime.Client.Close()
		delete(accounts.runtimes, account)
	}
	accounts.mutex.Unlock()
	accounts.workers.Wait()
}

func (runtime *Runtime) Request(ctx context.Context, method, target string, form url.Values) ([]byte, error) {
	requestContext, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	if runtime.Context.Err() != nil {
		return nil, store.ErrAccess
	}
	return runtime.Client.Request(requestContext, method, target, form)
}

func (accounts *Accounts) ResumeSaved() error {
	sessions, err := accounts.database.RestorableAccounts(accounts.context)
	if err != nil {
		return err
	}
	for _, session := range sessions {
		payload, err := accounts.database.ReadSecret(accounts.context, session.Account, "campus-session")
		if err != nil {
			continue
		}
		var saved struct {
			Account  string               `json:"account"`
			Password string               `json:"password"`
			Cookies  []campus.SavedCookie `json:"cookies"`
			Mode     string               `json:"mode"`
		}
		if json.Unmarshal(payload, &saved) != nil || saved.Account != session.Account || (saved.Mode != "direct" && saved.Mode != "webvpn") {
			continue
		}
		client, err := accounts.factory(session.Account)
		if err != nil {
			continue
		}
		if restorer, ok := client.(interface {
			Restore([]campus.SavedCookie, string)
		}); ok {
			restorer.Restore(saved.Cookies, saved.Mode)
		}
		runtimeContext, cancel := context.WithCancel(accounts.context)
		runtime := &Runtime{accounts: accounts, Client: client, Context: runtimeContext, Cancel: cancel, Generation: session.Generation}
		accounts.mutex.Lock()
		accounts.runtimes[session.Account] = runtime
		accounts.mutex.Unlock()
	}
	return accounts.restoreServiceOnly()
}
