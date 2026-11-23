package application

import (
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"testing"
)

func TestAgendaBrowserReconnect(t *testing.T) {
	if os.Getenv("TOOLBOX_BROWSER_TEST") != "1" {
		t.Skip("set TOOLBOX_BROWSER_TEST=1 to run browser integration")
	}
	api, handler := testApplication(t, "remote-multi")
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(t.Context(), account); err != nil {
			t.Fatal(err)
		}
	}
	webRoot := os.Getenv("TOOLBOX_BROWSER_WEB_ROOT")
	if webRoot == "" {
		webRoot = "../../frontend/build"
	}
	mux := http.NewServeMux()
	mux.Handle("/api/", handler)
	mux.Handle("/", http.FileServer(http.Dir(webRoot)))
	mux.HandleFunc("/timetable", func(response http.ResponseWriter, request *http.Request) {
		http.ServeFile(response, request, webRoot+"/index.html")
	})
	server := httptest.NewServer(mux)
	defer server.Close()
	command := exec.CommandContext(t.Context(), "node", "../../tools/check_agenda_reconnect.cjs", server.URL)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("browser check: %v\n%s", err, output)
	}
}
