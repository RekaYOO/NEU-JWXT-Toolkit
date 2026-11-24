package application

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type themeColors struct {
	Primary string `json:"primary"`
	Canvas  string `json:"canvas"`
	Surface string `json:"surface"`
	Text    string `json:"text"`
	Muted   string `json:"muted"`
	Border  string `json:"border"`
}

type themeTemplate struct {
	SchemaVersion int         `json:"schema_version"`
	ID            string      `json:"id"`
	Name          string      `json:"name"`
	Colors        themeColors `json:"colors"`
	Font          string      `json:"font"`
	Radius        int         `json:"radius"`
	Decoration    string      `json:"decoration"`
	Motion        string      `json:"motion"`
}

type appearancePreference struct {
	Selected  string          `json:"selected"`
	Templates []themeTemplate `json:"templates"`
}

var themeHex = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
var themeID = regexp.MustCompile(`^custom-[a-zA-Z0-9_-]{1,64}$`)

func builtinThemes() []themeTemplate {
	return []themeTemplate{
		{1, "sakura", "樱空", themeColors{"#a83264", "#fcf6f8", "#ffffff", "#30202a", "#705361", "#dbc6d0"}, "system", 8, "soft", "full"},
		{1, "blue", "晴蓝", themeColors{"#2563eb", "#f4f6f8", "#ffffff", "#1e293b", "#526277", "#d8e0e8"}, "system", 6, "none", "full"},
		{1, "garden", "青庭", themeColors{"#0f766e", "#f3f8f5", "#ffffff", "#18372f", "#4d695e", "#c7dcd2"}, "system", 8, "soft", "full"},
	}
}

func builtinTheme(identifier string) bool {
	for _, item := range builtinThemes() {
		if item.ID == identifier {
			return true
		}
	}
	return false
}

func themeLuminance(color string) float64 {
	value, _ := strconv.ParseUint(color[1:], 16, 32)
	channel := func(component uint64) float64 {
		normalized := float64(component) / 255
		if normalized <= 0.04045 {
			return normalized / 12.92
		}
		return math.Pow((normalized+0.055)/1.055, 2.4)
	}
	return 0.2126*channel(value>>16) + 0.7152*channel((value>>8)&255) + 0.0722*channel(value&255)
}

func themeContrast(first, second string) float64 {
	left, right := themeLuminance(first), themeLuminance(second)
	return (math.Max(left, right) + 0.05) / (math.Min(left, right) + 0.05)
}

func validateTheme(template themeTemplate) bool {
	if template.SchemaVersion != 1 || !themeID.MatchString(template.ID) || strings.TrimSpace(template.Name) != template.Name || utf8.RuneCountInString(template.Name) < 1 || utf8.RuneCountInString(template.Name) > 40 {
		return false
	}
	for _, char := range template.Name {
		if unicode.IsControl(char) {
			return false
		}
	}
	if template.Font != "system" && template.Font != "serif" && template.Font != "mono" {
		return false
	}
	if template.Radius < 0 || template.Radius > 16 || (template.Decoration != "none" && template.Decoration != "soft") || (template.Motion != "full" && template.Motion != "reduced") {
		return false
	}
	colors := template.Colors
	for _, color := range []string{colors.Primary, colors.Canvas, colors.Surface, colors.Text, colors.Muted, colors.Border} {
		if !themeHex.MatchString(color) {
			return false
		}
	}
	for _, pair := range [][2]string{{colors.Text, colors.Canvas}, {colors.Text, colors.Surface}, {colors.Muted, colors.Canvas}, {colors.Muted, colors.Surface}, {colors.Primary, "#ffffff"}, {colors.Primary, colors.Surface}} {
		if themeContrast(pair[0], pair[1]) < 4.5 {
			return false
		}
	}
	return true
}

func validateAppearance(preference appearancePreference) bool {
	if len(preference.Templates) > 32 {
		return false
	}
	seen := map[string]bool{}
	for _, template := range preference.Templates {
		if seen[template.ID] || !validateTheme(template) {
			return false
		}
		seen[template.ID] = true
	}
	return preference.Selected == "" || builtinTheme(preference.Selected) || seen[preference.Selected]
}

func (api *API) siteTheme(ctx context.Context) (string, error) {
	payload, err := api.database.SiteSetting(ctx, "appearance-default")
	if errors.Is(err, store.ErrMissing) {
		return "blue", nil
	}
	if err != nil {
		return "", err
	}
	if !builtinTheme(string(payload)) {
		return "blue", nil
	}
	return string(payload), nil
}

func (api *API) registerAppearance(router *http.ServeMux) {
	router.HandleFunc("GET /api/appearance", api.appearance)
	router.HandleFunc("PUT /api/appearance", api.appearance)
	router.HandleFunc("GET /api/admin/appearance", api.adminAppearance)
	router.HandleFunc("PUT /api/admin/appearance", api.adminAppearance)
}

func (api *API) appearance(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	preference := appearancePreference{Templates: []themeTemplate{}}
	document, err := api.database.Read(request.Context(), session.Account, "theme", "appearance")
	if err != nil && !errors.Is(err, store.ErrMissing) {
		api.documentError(response, err)
		return
	}
	if err == nil && !document.Deleted {
		if json.Unmarshal(document.Payload, &preference) != nil || !validateAppearance(preference) {
			api.documentError(response, store.ErrConflict)
			return
		}
	}
	if request.Method == http.MethodPut {
		var input struct {
			Revision  *int64          `json:"revision"`
			Selected  string          `json:"selected"`
			Templates []themeTemplate `json:"templates"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		preference = appearancePreference{Selected: input.Selected, Templates: input.Templates}
		if input.Revision == nil || *input.Revision < 0 || !validateAppearance(preference) {
			writeJSON(response, 422, map[string]string{"detail": "主题模板无效，请检查格式、名称和文字对比度"})
			return
		}
		if preference.Templates == nil {
			preference.Templates = []themeTemplate{}
		}
		payload, _ := json.Marshal(preference)
		document, err = api.database.SaveForSession(request.Context(), session, "theme", "appearance", payload, *input.Revision, false)
		if err != nil {
			api.documentError(response, err)
			return
		}
	}
	defaultTheme, err := api.siteTheme(request.Context())
	if err != nil {
		api.documentError(response, err)
		return
	}
	effective := preference.Selected
	if effective == "" {
		effective = defaultTheme
	}
	writeJSON(response, 200, map[string]any{"revision": document.Revision, "selected": preference.Selected, "templates": preference.Templates, "builtins": builtinThemes(), "site_default": defaultTheme, "effective": effective})
}

func (api *API) adminAppearance(response http.ResponseWriter, request *http.Request) {
	if !api.requireAdmin(response, request) {
		return
	}
	if request.Method == http.MethodPut {
		var input struct {
			Default  string `json:"site_default"`
			Previous string `json:"previous"`
		}
		if !decodeJSON(response, request, &input) {
			return
		}
		if !builtinTheme(input.Default) || !builtinTheme(input.Previous) {
			writeJSON(response, 422, map[string]string{"detail": "请选择内置站点模板"})
			return
		}
		previous := input.Previous
		if previous == "blue" {
			stored, err := api.database.SiteSetting(request.Context(), "appearance-default")
			if err != nil && !errors.Is(err, store.ErrMissing) {
				api.documentError(response, err)
				return
			}
			if err == nil && !builtinTheme(string(stored)) {
				previous = string(stored)
			}
		}
		if err := api.database.SaveThemeDefault(request.Context(), previous, input.Default); err != nil {
			api.documentError(response, err)
			return
		}
	}
	selected, err := api.siteTheme(request.Context())
	if err != nil {
		api.documentError(response, err)
		return
	}
	writeJSON(response, 200, map[string]any{"site_default": selected, "builtins": builtinThemes()})
}
