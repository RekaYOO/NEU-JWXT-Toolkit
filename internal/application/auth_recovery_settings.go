package application

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type recoverySettings struct {
	BaseURL string `json:"public_base_url"`
	Hours   int    `json:"link_ttl_hours"`
	Epoch   string `json:"epoch,omitempty"`
}

func (settings recoverySettings) public() map[string]any {
	return map[string]any{"public_base_url": settings.BaseURL, "link_ttl_hours": settings.Hours}
}

func (api *API) readRecoverySettings(ctx context.Context, session store.Session) (recoverySettings, int64, error) {
	settings := recoverySettings{Hours: 3}
	document, err := api.database.Read(ctx, session.Account, "auth-recovery", "config")
	if errors.Is(err, store.ErrMissing) {
		return settings, 0, nil
	}
	if err != nil {
		return settings, 0, err
	}
	if document.Deleted || json.Unmarshal(document.Payload, &settings) != nil {
		return settings, document.Revision, store.ErrConflict
	}
	return settings, document.Revision, nil
}

func validRecoveryBase(value string, remote bool) bool {
	if value == "" {
		return true
	}
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(value, "#") || parsed.Path != "" || parsed.Opaque != "" {
		return false
	}
	if port := parsed.Port(); port != "" {
		number, err := strconv.Atoi(port)
		if err != nil || number < 1 || number > 65535 {
			return false
		}
	} else if strings.HasSuffix(parsed.Host, ":") {
		return false
	}
	if parsed.Scheme == "https" {
		return true
	}
	address := net.ParseIP(parsed.Hostname())
	return !remote && parsed.Scheme == "http" && (parsed.Hostname() == "localhost" || address != nil && address.IsLoopback())
}

func (api *API) recoverySettings(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	lockValue, _ := api.accounts.loginLocks.LoadOrStore(session.Account, &sync.Mutex{})
	lock := lockValue.(*sync.Mutex)
	lock.Lock()
	defer lock.Unlock()
	settings, revision, err := api.readRecoverySettings(request.Context(), session)
	if err != nil {
		api.documentError(response, err)
		return
	}
	if request.Method == "GET" {
		writeJSON(response, 200, settings.public())
		return
	}
	input := struct {
		BaseURL *string `json:"public_base_url"`
		Hours   *int    `json:"link_ttl_hours"`
	}{}
	if !decodeJSON(response, request, &input) {
		return
	}
	previous := settings.BaseURL
	if input.BaseURL != nil {
		settings.BaseURL = strings.TrimRight(strings.TrimSpace(*input.BaseURL), "/")
	}
	if input.Hours != nil {
		settings.Hours = *input.Hours
	}
	if !validRecoveryBase(settings.BaseURL, api.accounts.mode != "local-single") || settings.Hours < 1 || settings.Hours > 168 {
		writeJSON(response, 422, map[string]string{"detail": "请填写HTTPS站点根地址及1至168小时的整数有效期；本机调试可使用回环HTTP地址"})
		return
	}
	if settings.Epoch == "" || previous != settings.BaseURL {
		identifier := make([]byte, 16)
		if _, err := rand.Read(identifier); err != nil {
			api.documentError(response, err)
			return
		}
		settings.Epoch = hex.EncodeToString(identifier)
	}
	payload, _ := json.Marshal(settings)
	if _, err := api.database.SaveForSession(request.Context(), session, "auth-recovery", "config", payload, revision, false); err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"success": true, "config": settings.public()})
}
