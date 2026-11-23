package campus

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/hex"
	"errors"
	"net/url"
	"strings"
)

const vpnHost = "webvpn.neu.edu.cn"
const vpnPrefix = "62304135386136393339346365373340"

func encryptedHostname(hostname string) string {
	key := []byte("b0A58a69394ce73@")
	block, _ := aes.NewCipher(key)
	encoded := make([]byte, len(hostname))
	cipher.NewCFBEncrypter(block, key).XORKeyStream(encoded, []byte(hostname))
	return hex.EncodeToString(encoded)
}

func WebVPNURL(raw string) (string, error) {
	target, err := url.Parse(raw)
	if err != nil || target.Hostname() == "" || target.User != nil || (target.Scheme != "http" && target.Scheme != "https") {
		return "", errors.New("WebVPN requires an HTTP(S) URL without credentials")
	}
	if strings.EqualFold(target.Hostname(), vpnHost) {
		return target.String(), nil
	}
	protocol := target.Scheme
	if port := target.Port(); port != "" {
		protocol += "-" + port
	}
	prefix := "/" + protocol + "/" + vpnPrefix + encryptedHostname(strings.ToLower(target.Hostname()))
	target.RawPath = prefix + target.EscapedPath()
	target.Path = prefix + target.Path
	target.Scheme = "https"
	target.Host = vpnHost
	return target.String(), nil
}

func RestoreServiceURL(raw, origin string) (string, error) {
	base, err := url.Parse(origin)
	if err != nil || base.Scheme != "https" || base.Hostname() == "" || base.User != nil {
		return "", errors.New("invalid registered service")
	}
	reference, err := url.Parse(raw)
	if err != nil || strings.Contains(raw, "\\") || strings.ContainsAny(raw, "\x00\r\n\t") {
		return "", errors.New("unsafe service path")
	}
	if strings.Contains(reference.Path, "%") {
		return "", errors.New("nested path encoding is not allowed")
	}
	for _, part := range strings.Split(reference.Path, "/") {
		if part == "." || part == ".." {
			return "", errors.New("unsafe service path")
		}
	}
	target := base.ResolveReference(reference)
	if target.Scheme != "https" || target.User != nil || (target.Port() != "" && target.Port() != "443") {
		return "", errors.New("untrusted service URL")
	}
	proxyPath := strings.HasPrefix(target.Path, "/https/") || strings.HasPrefix(target.Path, "/https-443/") || strings.HasPrefix(target.Path, "/http/")
	if target.Hostname() == vpnHost || proxyPath {
		if target.Hostname() != vpnHost && target.Hostname() != base.Hostname() {
			return "", errors.New("untrusted proxy host")
		}
		matched := false
		for _, protocol := range []string{"https", "https-443"} {
			prefix := "/" + protocol + "/" + vpnPrefix + encryptedHostname(base.Hostname()) + "/"
			if strings.HasPrefix(target.Path, prefix) {
				target.Path = "/" + strings.TrimPrefix(target.Path, prefix)
				target.RawPath = ""
				matched = true
				break
			}
		}
		if !matched {
			return "", errors.New("untrusted proxy target")
		}
	} else if target.Hostname() != base.Hostname() {
		return "", errors.New("untrusted service host")
	}
	target.Host = base.Host
	return target.String(), nil
}
