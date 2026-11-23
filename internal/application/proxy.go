package application

import (
	"fmt"
	"net"
	"net/http"
	"net/netip"
	"strings"
)

func (api *API) SetTrustedProxies(config string) error {
	proxies := []netip.Prefix{}
	if strings.TrimSpace(config) != "" {
		for _, value := range strings.Split(config, ",") {
			prefix, err := netip.ParsePrefix(strings.TrimSpace(value))
			if err != nil || prefix.Bits() == 0 {
				return fmt.Errorf("trusted proxies must be explicit non-global IP prefixes")
			}
			proxies = append(proxies, prefix.Masked())
		}
	}
	api.trustedProxies = proxies
	return nil
}

func (api *API) clientSource(request *http.Request) string {
	host, _, err := net.SplitHostPort(request.RemoteAddr)
	if err != nil {
		host = request.RemoteAddr
	}
	peer, err := netip.ParseAddr(host)
	if err != nil {
		return host
	}
	peer = peer.Unmap()
	trusted := func(address netip.Addr) bool {
		for _, prefix := range api.trustedProxies {
			if prefix.Contains(address) {
				return true
			}
		}
		return false
	}
	forwarded := request.Header.Get("X-Forwarded-For")
	if !trusted(peer) || len(forwarded) > 4096 || forwarded == "" {
		return peer.String()
	}
	hops := strings.Split(forwarded, ",")
	if len(hops) > 16 {
		return peer.String()
	}
	current := peer
	for index := len(hops) - 1; index >= 0 && trusted(current); index-- {
		address, err := netip.ParseAddr(strings.TrimSpace(hops[index]))
		if err != nil {
			return peer.String()
		}
		current = address.Unmap()
	}
	return current.String()
}
