package main

import "testing"

func TestDeploymentNetworkBoundaries(t *testing.T) {
	for _, item := range []struct {
		listen, mode, origin string
		container, valid     bool
	}{
		{"127.0.0.1:8000", "local-single", "", false, true},
		{"0.0.0.0:8000", "local-single", "", true, false},
		{"127.0.0.1:8000", "remote-multi", "", false, false},
		{"127.0.0.1:8000", "remote-single", "http://toolbox.example", false, false},
		{"127.0.0.1:8000", "remote-multi", "https://toolbox.example", false, true},
		{"0.0.0.0:8000", "remote-multi", "https://toolbox.example", false, false},
		{"0.0.0.0:8000", "remote-multi", "https://toolbox.example", true, true},
		{"127.0.0.1:8000", "remote-multi", "https://user:secret@toolbox.example", false, false},
		{"127.0.0.1:8000", "remote-multi", "https://toolbox.example/path", false, false},
		{"127.0.0.1:8000", "unknown", "", false, false},
	} {
		err := validateDeployment(item.listen, item.mode, item.origin, item.container)
		if (err == nil) != item.valid {
			t.Fatalf("deployment validation: mode=%s listen=%s err=%v", item.mode, item.listen, err)
		}
	}
}
