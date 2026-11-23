package application

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"net"
	"net/http"
	"net/url"
	"time"
)

func (api *API) ConfigureDesktopShutdown(host string, shutdown func()) error {
	address, _, err := net.SplitHostPort(host)
	parsed := net.ParseIP(address)
	if api.accounts.mode != "local-single" || err != nil || parsed == nil || !parsed.IsLoopback() || shutdown == nil {
		return errors.New("desktop shutdown requires a local loopback listener")
	}
	token := make([]byte, 32)
	if _, err := rand.Read(token); err != nil {
		return err
	}
	api.desktopHost, api.desktopToken, api.desktopShutdown = host, hex.EncodeToString(token), shutdown
	return nil
}

func (api *API) authorizeDesktopAction(response http.ResponseWriter, request *http.Request) bool {
	if api.accounts.mode != "local-single" || api.desktopShutdown == nil {
		writeJSON(response, 404, map[string]string{"detail": "当前模式不支持本机程序操作"})
		return false
	}
	if request.Host != api.desktopHost || subtle.ConstantTimeCompare([]byte(request.Header.Get("X-NEU-Shutdown-Token")), []byte(api.desktopToken)) != 1 {
		writeJSON(response, 403, map[string]string{"detail": "本机请求验证失败"})
		return false
	}
	if origin := request.Header.Get("Origin"); origin != "" {
		parsed, err := url.Parse(origin)
		if err != nil || parsed.Scheme != "http" || parsed.Host != api.desktopHost || parsed.User != nil || parsed.Path != "" && parsed.Path != "/" || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" {
			writeJSON(response, 403, map[string]string{"detail": "本机请求来源无效"})
			return false
		}
	}
	return true
}

func (api *API) shutdownDesktop(response http.ResponseWriter, request *http.Request) {
	if !api.authorizeDesktopAction(response, request) {
		return
	}
	writeJSON(response, 200, map[string]bool{"success": true})
	api.desktopShutdownOnce.Do(func() { time.AfterFunc(100*time.Millisecond, api.desktopShutdown) })
}
