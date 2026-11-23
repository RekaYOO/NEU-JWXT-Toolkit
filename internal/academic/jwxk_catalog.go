package academic

import (
	"context"
	"regexp"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKTimeSlot struct {
	Weekday int `json:"weekday"`
	Section int `json:"section"`
}

type JWXKCatalogSearch struct {
	Batch      string            `json:"batch_code"`
	Page       int               `json:"page_number"`
	Size       int               `json:"page_size"`
	Keyword    string            `json:"keyword"`
	Scope      string            `json:"scope"`
	Campus     string            `json:"campus"`
	Order      string            `json:"order_by"`
	Filters    map[string]string `json:"filters"`
	TimeSlot   *JWXKTimeSlot     `json:"time_slot"`
	OnlineMode string            `json:"online_mode"`
	LocalOnly  bool              `json:"local_only"`
}

func (input JWXKCatalogSearch) Valid() bool {
	probe := JWXKSearch{Batch: input.Batch, Scope: "FANKC", Page: input.Page, Size: input.Size, Keyword: input.Keyword, Campus: input.Campus, Order: input.Order, Filters: input.Filters}
	return probe.Valid() && jwxkScopeCode.MatchString(input.Scope) && (input.OnlineMode == "all" || input.OnlineMode == "online" || input.OnlineMode == "offline") && (input.TimeSlot == nil || input.TimeSlot.Weekday >= 1 && input.TimeSlot.Weekday <= 7 && input.TimeSlot.Section >= 1 && input.TimeSlot.Section <= 30)
}

var jwxkCategoryEnding = regexp.MustCompile(`(?:课程|课|类|模块)$`)

func matchesJWXKCatalog(course map[string]any, input JWXKCatalogSearch) bool {
	if input.OnlineMode != "all" && strings.Contains(Text(course["course_name"]), "在线式") != (input.OnlineMode == "online") {
		return false
	}
	if requested := input.Filters["KCLB"]; requested != "" {
		key := jwxkCategoryEnding.ReplaceAllString(jwxkCategory(requested), "")
		matched := false
		for _, category := range append(jwxkStrings(course["course_categories"]), Text(course["course_category"])) {
			if key != "" && key == jwxkCategoryEnding.ReplaceAllString(jwxkCategory(category), "") {
				matched = true
			}
		}
		if !matched {
			return false
		}
	}
	meetings := jwxkArchiveMeetings(course["schedules"])
	if input.TimeSlot != nil {
		matched := false
		for _, meeting := range meetings {
			if int(Number(meeting["weekday"])) == input.TimeSlot.Weekday && Number(meeting["start_section"]) > 0 && int(Number(meeting["start_section"])) <= input.TimeSlot.Section && int(Number(meeting["end_section"])) >= input.TimeSlot.Section {
				matched = true
			}
		}
		if !matched {
			return false
		}
	}
	if input.Campus != "" {
		campus := jwxkCampus(input.Campus)
		matched := jwxkCampus(course["campus"]) == campus || jwxkCampus(course["campus_name"]) == campus
		for _, meeting := range meetings {
			matched = matched || jwxkCampus(meeting["campus"]) == campus || jwxkCampus(meeting["campus_name"]) == campus
		}
		if !matched {
			return false
		}
	}
	return true
}

func SearchJWXKCatalog(ctx context.Context, requester ServiceRequester, mode string, input JWXKCatalogSearch) (map[string]any, error) {
	if !input.Valid() || input.LocalOnly {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "远端目录查询参数无效"}
	}
	contextData, err := FetchJWXKContext(ctx, requester, mode)
	if err != nil {
		return nil, err
	}
	var batch map[string]any
	for _, candidate := range contextData["batches"].([]map[string]any) {
		if Text(candidate["code"]) == input.Batch {
			batch = candidate
			break
		}
	}
	if batch == nil {
		return nil, &campus.Error{Code: "JWXK_BATCH_MISSING", Message: "选课轮次不存在或当前账号不可见"}
	}
	menus := batch["menus"].([]map[string]string)
	if len(menus) == 0 {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "尚未取得本轮课程范围，不能据此判断本轮无课"}
	}
	scope := input.Scope
	found := scope == "ALL" || scope == "ROUND"
	for _, menu := range menus {
		found = found || menu["code"] == scope
	}
	if !found {
		scope = "ALL"
	}
	aggregate := scope == "ALL" || scope == "ROUND"
	scopes := []string{scope}
	if aggregate {
		scopes = []string{}
		for _, menu := range menus {
			if menu["code"] != "ALLKC" {
				scopes = append(scopes, menu["code"])
			}
		}
		scopes = uniqueJWXK(scopes)
	}
	filtered := input.Filters["KCLB"] != "" || input.TimeSlot != nil || input.OnlineMode != "all" || input.Campus != "" && (aggregate || scope == "ALLKC")
	filters := map[string]string{}
	for key, value := range input.Filters {
		if key != "KCLB" {
			filters[key] = value
		}
	}
	if input.TimeSlot != nil {
		filters["SKXQ"] = strconv.Itoa(input.TimeSlot.Weekday)
	}
	courses := []map[string]any{}
	byClass := map[string]map[string]any{}
	total := 0
	for _, currentScope := range scopes {
		page, size := input.Page, input.Size
		if filtered {
			page, size = 1, 50
		}
		for {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			result, err := SearchJWXKCourses(ctx, requester, mode, JWXKSearch{Batch: input.Batch, Scope: currentScope, Page: page, Size: size, Keyword: input.Keyword, Campus: input.Campus, Order: input.Order, Filters: filters})
			if err != nil {
				return nil, err
			}
			remoteTotal := int(Number(result["total"]))
			if !filtered || page == 1 {
				total += remoteTotal
			}
			for _, course := range result["courses"].([]map[string]any) {
				if filtered && !matchesJWXKCatalog(course, input) {
					continue
				}
				course["source_scopes"] = []string{currentScope}
				course["source_tags"] = []string{firstText(jwxkMenuNames[currentScope], "其他课程")}
				if Text(course["teaching_class_type"]) == "" && currentScope != "ALLKC" {
					course["teaching_class_type"] = currentScope
				}
				key := Text(course["class_id"])
				if key == "" {
					key = Text(course["course_code"]) + "|" + Text(course["teacher"]) + "|" + Text(course["class_number"])
				}
				if previous := byClass[key]; previous != nil {
					for _, field := range []string{"source_scopes", "source_tags", "course_categories"} {
						previous[field] = uniqueJWXK(append(jwxkStrings(previous[field]), jwxkStrings(course[field])...))
					}
					for field, value := range course {
						if field == "source_scopes" || field == "source_tags" || field == "course_categories" || field == "teaching_class_type" {
							continue
						}
						if value != nil && Text(value) != "" && Text(value) != "[]" {
							previous[field] = value
						}
					}
				} else {
					byClass[key] = course
					courses = append(courses, course)
				}
			}
			if !filtered || page*size >= remoteTotal {
				break
			}
			if page >= 60 {
				return nil, &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "课程范围过大，尚未完整读取，请缩小查询条件"}
			}
			page++
		}
	}
	ApplyJWXKMarket(courses, Text(batch["selection_type_code"]))
	groups := GroupJWXKCourses(courses)
	if filtered {
		total = len(groups)
		start := min((input.Page-1)*input.Size, len(groups))
		groups = groups[start:min(start+input.Size, len(groups))]
	}
	options := []map[string]string{{"code": "ALL", "name": "所有课程"}, {"code": "ROUND", "name": "本轮课程"}}
	options = append(options, menus...)
	return map[string]any{"total": total, "groups": groups, "scope": scope, "scope_options": options, "cache_hit": false, "data_source": "remote", "sync_status": "", "catalog_complete": false}, nil
}
