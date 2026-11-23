package application

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func TestAppearanceTemplatesValidationAndAccountIsolation(t *testing.T) {
	api, handler := testApplication(t, "remote-multi")
	ctx := context.Background()
	for _, account := range []string{"student-a", "student-b"} {
		if err := api.database.AddAccount(ctx, account); err != nil {
			t.Fatal(err)
		}
	}
	cookie := loginCookie(t, handler, "student-a")
	other := loginCookie(t, handler, "student-b")
	if response := apiRequest(handler, "GET", "/api/appearance", "", nil); response.Code != 401 {
		t.Fatal("anonymous access", response.Code)
	}
	if response := apiRequest(handler, "PUT", "/api/admin/appearance", `{"previous":"blue","site_default":"garden"}`, cookie); response.Code != 401 {
		t.Fatal("school session used as admin", response.Code)
	}
	if err := api.database.SaveThemeDefault(ctx, "blue", "garden"); err != nil {
		t.Fatal(err)
	}
	api.gatewayRequired = true
	public := apiRequest(handler, "GET", "/api/access/status", "", nil)
	var publicStatus map[string]any
	json.Unmarshal(public.Body.Bytes(), &publicStatus)
	if public.Code != 200 || publicStatus["site_default"] != "garden" || publicStatus["templates"] != nil {
		t.Fatal("public theme missing or private data exposed", public.Body.String())
	}
	api.gatewayRequired = false
	if err := api.database.SaveThemeDefault(ctx, "blue", "sakura"); err != store.ErrConflict {
		t.Fatal("stale site default overwrote current", err)
	}
	response := apiRequest(handler, "GET", "/api/appearance", "", cookie)
	var initial map[string]any
	json.Unmarshal(response.Body.Bytes(), &initial)
	if response.Code != 200 || initial["effective"] != "garden" {
		t.Fatal(response.Body.String())
	}
	custom := builtinThemes()[0]
	custom.ID, custom.Name = "custom-one", "个人主题"
	for _, builtin := range builtinThemes() {
		builtin.ID = "custom-check"
		if !validateTheme(builtin) {
			t.Fatal("unreadable builtin", builtin.Name)
		}
	}
	encode := func(revision int64, selected string, templates []themeTemplate) string {
		payload, _ := json.Marshal(map[string]any{"revision": revision, "selected": selected, "templates": templates})
		return string(payload)
	}
	valid := encode(0, custom.ID, []themeTemplate{custom})
	response = apiRequest(handler, "PUT", "/api/appearance", valid, cookie)
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	if result := apiRequest(handler, "PUT", "/api/appearance", valid, cookie); result.Code != 409 {
		t.Fatal("lost-update protection missing", result.Code)
	}
	otherResponse := apiRequest(handler, "GET", "/api/appearance", "", other)
	var otherData map[string]any
	json.Unmarshal(otherResponse.Body.Bytes(), &otherData)
	if otherData["effective"] != "garden" || len(otherData["templates"].([]any)) != 0 {
		t.Fatal("private templates leaked", otherResponse.Body.String())
	}
	otherTemplate := custom
	otherTemplate.Name = "另一账号的主题"
	if result := apiRequest(handler, "PUT", "/api/appearance", encode(0, otherTemplate.ID, []themeTemplate{otherTemplate}), other); result.Code != 200 {
		t.Fatal("same template ID should be account scoped", result.Body.String())
	}
	for _, owner := range []struct {
		cookie *http.Cookie
		name   string
	}{{cookie, custom.Name}, {other, otherTemplate.Name}} {
		result := apiRequest(handler, "GET", "/api/appearance", "", owner.cookie)
		var actual struct {
			Revision  int64           `json:"revision"`
			Templates []themeTemplate `json:"templates"`
		}
		if result.Code != 200 || json.Unmarshal(result.Body.Bytes(), &actual) != nil || actual.Revision != 1 || len(actual.Templates) != 1 || actual.Templates[0].ID != custom.ID || actual.Templates[0].Name != owner.name {
			t.Fatal("account-scoped save or revision changed", result.Body.String())
		}
	}
	for _, mutate := range []func(*themeTemplate){
		func(item *themeTemplate) { item.ID = "blue" },
		func(item *themeTemplate) { item.Font = "url(https://example.com/font)" },
		func(item *themeTemplate) { item.Colors.Primary = "red; background:url(test)" },
		func(item *themeTemplate) { item.Colors.Text = item.Colors.Surface },
		func(item *themeTemplate) { item.Radius = 17 },
		func(item *themeTemplate) { item.SchemaVersion = 2 },
		func(item *themeTemplate) { item.Motion = "script" },
	} {
		invalid := custom
		mutate(&invalid)
		if result := apiRequest(handler, "PUT", "/api/appearance", encode(1, invalid.ID, []themeTemplate{invalid}), cookie); result.Code != 422 {
			t.Fatal("invalid template accepted", result.Code)
		}
	}
	if result := apiRequest(handler, "PUT", "/api/appearance", encode(1, "custom-missing", nil), cookie); result.Code != 422 {
		t.Fatal("dangling selection")
	}
	for _, method := range []string{"PUT", "DELETE"} {
		if result := apiRequest(handler, method, "/api/documents/theme/appearance", `{"revision":1,"data":{}}`, cookie); result.Code != 403 {
			t.Fatal("generic bypass")
		}
	}
	response = apiRequest(handler, "GET", "/api/appearance", "", cookie)
	var saved map[string]any
	json.Unmarshal(response.Body.Bytes(), &saved)
	if saved["effective"] != custom.ID || saved["revision"] != float64(1) {
		t.Fatal("invalid import overwrote preference")
	}
	secondDevice := loginCookie(t, handler, "student-a")
	response = apiRequest(handler, "GET", "/api/appearance", "", secondDevice)
	json.Unmarshal(response.Body.Bytes(), &saved)
	if saved["effective"] != custom.ID {
		t.Fatal("new session lost theme")
	}
	response = apiRequest(handler, "PUT", "/api/appearance", encode(1, "", nil), secondDevice)
	json.Unmarshal(response.Body.Bytes(), &saved)
	if response.Code != 200 || saved["effective"] != "garden" || len(saved["templates"].([]any)) != 0 {
		t.Fatal("reset or delete failed", response.Body.String())
	}
	apiRequest(handler, "POST", "/api/logout", "", secondDevice)
	if result := apiRequest(handler, "PUT", "/api/appearance", encode(2, "blue", nil), secondDevice); result.Code != 401 {
		t.Fatal("revoked write")
	}
}

func TestSiteAppearanceRequiresAdminAndKeepsPersonalPreference(t *testing.T) {
	api, handler := testApplication(t, "local-single")
	ctx := context.Background()
	if err := api.database.SetSitePassword(ctx, "admin", "synthetic-password", false); err != nil {
		t.Fatal(err)
	}
	token, err := api.database.CreateSiteSession(ctx, "admin", "synthetic-password", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	admin := &http.Cookie{Name: siteCookie("admin"), Value: token}
	response := apiRequest(handler, "GET", "/api/admin/appearance", "", admin)
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	cookie := loginCookie(t, handler, "student")
	response = apiRequest(handler, "PUT", "/api/appearance", `{"revision":0,"selected":"sakura","templates":[]}`, cookie)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "PUT", "/api/admin/appearance", `{"previous":"blue","site_default":"garden"}`, admin)
	if response.Code != 200 {
		t.Fatal(response.Body.String())
	}
	response = apiRequest(handler, "GET", "/api/appearance", "", cookie)
	var preference map[string]any
	json.Unmarshal(response.Body.Bytes(), &preference)
	if preference["effective"] != "sakura" || preference["site_default"] != "garden" {
		t.Fatal("site default overwrote account", response.Body.String())
	}
	if response := apiRequest(handler, "PUT", "/api/admin/appearance", `{"previous":"blue","site_default":"sakura"}`, admin); response.Code != 409 {
		t.Fatal("stale admin save")
	}
	if err := api.database.SaveThemeDefault(ctx, "garden", "invalid-old-template"); err != nil {
		t.Fatal(err)
	}
	response = apiRequest(handler, "GET", "/api/access/status", "", nil)
	var public map[string]any
	json.Unmarshal(response.Body.Bytes(), &public)
	if response.Code != 200 || public["site_default"] != "blue" {
		t.Fatal("invalid appearance blocked login", response.Body.String())
	}
}
