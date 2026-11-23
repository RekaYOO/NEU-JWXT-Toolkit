package academic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func outlinePost(ctx context.Context, requester Requester, path string, form url.Values) (map[string]any, error) {
	encoded, err := requester.Request(ctx, http.MethodPost, "https://jwxt.neu.edu.cn/jwapp/sys/kccx/"+path, form)
	if err != nil {
		return nil, err
	}
	payload, err := Decode(encoded)
	if err != nil {
		return nil, err
	}
	if value, exists := payload["code"]; exists && Text(value) != "0" {
		return nil, &campus.Error{Code: "OUTLINE_RESPONSE_REJECTED", Message: "学校未确认大纲查询成功"}
	}
	return payload, nil
}

func outlineRows(payload map[string]any, action string) ([]map[string]any, map[string]any, error) {
	container := payload
	if nested := Object(Object(payload["datas"])[action]); nested != nil {
		container = nested
	}
	if row := Object(container["rows"]); row != nil {
		return []map[string]any{row}, container, nil
	}
	if container["rows"] == nil {
		return nil, container, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "大纲列表结构异常"}
	}
	rows, err := reportRows(container["rows"])
	return rows, container, err
}

func outlineDisplay(row map[string]any, field string) string {
	return strings.TrimSpace(firstText(row[field+"_DISPLAY"], row[field+"DISPLAY"], row[field]))
}

type OutlineSearch struct {
	Keyword  string         `json:"keyword"`
	Filters  map[string]any `json:"filters"`
	Page     int            `json:"page"`
	PageSize int            `json:"page_size"`
}

func (query OutlineSearch) Valid() bool {
	if len([]rune(query.Keyword)) > 80 || query.Page < 1 || query.Page > 10000 || query.PageSize < 1 || query.PageSize > 100 {
		return false
	}
	allowed := map[string]bool{"KCH": true, "KCM": true, "KKDWDM": true, "KCCCDM": true, "KCJBDM": true, "XF": true, "XS": true}
	for key, value := range query.Filters {
		if !allowed[key] {
			return false
		}
		switch typed := value.(type) {
		case nil, string, json.Number, float64:
			if len(Text(value)) > 160 {
				return false
			}
		case []any:
			if (key != "XF" && key != "XS") || (len(typed) != 0 && len(typed) != 2) {
				return false
			}
			for _, bound := range typed {
				switch bound.(type) {
				case nil, string, json.Number, float64:
					if len(Text(bound)) > 32 {
						return false
					}
				default:
					return false
				}
			}
		default:
			return false
		}
	}
	return true
}

func SearchOutlines(ctx context.Context, requester Requester, query OutlineSearch) (map[string]any, error) {
	if !query.Valid() {
		return nil, &campus.Error{Code: "INVALID_OUTLINE_QUERY", Message: "大纲查询条件无效"}
	}
	rules := []map[string]string{}
	add := func(field, value, builder string) {
		rules = append(rules, map[string]string{"name": field, "value": value, "builder": builder, "linkOpt": "AND"})
	}
	if keyword := strings.TrimSpace(query.Keyword); keyword != "" {
		field := "KCM"
		if courseCodePattern.MatchString(keyword) {
			field = "KCH"
		}
		add(field, keyword, "include")
	}
	for _, field := range []string{"KCH", "KCM", "KKDWDM", "KCCCDM", "KCJBDM", "XF", "XS"} {
		value := query.Filters[field]
		if value == nil || Text(value) == "" {
			continue
		}
		if bounds, ok := value.([]any); ok {
			if len(bounds) == 2 && (Text(bounds[0]) != "" || Text(bounds[1]) != "") {
				add(field, Text(bounds[0])+","+Text(bounds[1]), "between")
			}
		} else {
			add(field, Text(value), "equal")
		}
	}
	encoded, _ := json.Marshal(rules)
	payload, err := outlinePost(ctx, requester, "modules/dgcx/cxlb.do", url.Values{"querySetting": {string(encoded)}, "pageNumber": {strconv.Itoa(query.Page)}, "pageSize": {strconv.Itoa(query.PageSize)}, "*order": {"+KCH"}})
	if err != nil {
		return nil, err
	}
	rows, container, err := outlineRows(payload, "cxlb")
	if err != nil {
		return nil, err
	}
	items := []map[string]any{}
	for _, row := range rows {
		code := strings.TrimSpace(Text(row["KCH"]))
		if code != "" {
			items = append(items, map[string]any{"course_code": code, "course_name": outlineDisplay(row, "KCM"), "department": outlineDisplay(row, "KKDWDM"), "level": outlineDisplay(row, "KCCCDM"), "grade": outlineDisplay(row, "KCJBDM"), "credits": row["XF"], "hours": row["XS"]})
		}
	}
	total := len(items)
	if value, exists := container["totalSize"]; exists {
		parsed, err := strconv.Atoi(Text(value))
		if err != nil || parsed < 0 {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "大纲总数字段异常"}
		}
		total = parsed
	}
	return map[string]any{"items": items, "total": total, "page": query.Page, "page_size": query.PageSize}, nil
}

func OutlineOverview(ctx context.Context, requester Requester, code string) (map[string]any, error) {
	if !ValidCourseCode(code) {
		return nil, &campus.Error{Code: "INVALID_COURSE_CODE", Message: "课程代码无效"}
	}
	if _, err := outlinePost(ctx, requester, "api/kcdgwhgl/cshdgsj.do", url.Values{"KCH": {code}}); err != nil {
		return nil, err
	}
	sections, failures := map[string]map[string]any{}, map[string]string{}
	for _, section := range []struct{ key, action string }{{"basic", "cxkcxxx"}, {"outline", "cxkcdgxx"}, {"introduction", "cxkcjcxx"}} {
		payload, err := outlinePost(ctx, requester, "modules/kcdgwhgl/"+section.action+".do", url.Values{"KCH": {code}})
		if err != nil {
			failures[section.key] = "unavailable"
			continue
		}
		rows, _, err := outlineRows(payload, section.action)
		if err != nil {
			failures[section.key] = "invalid_response"
			continue
		}
		if len(rows) > 0 {
			sections[section.key] = rows[0]
		}
	}
	basic, outline, intro := sections["basic"], sections["outline"], sections["introduction"]
	return map[string]any{"course_code": code, "course_name": outlineDisplay(basic, "KCM"), "department": outlineDisplay(basic, "KKDWDM"), "credits": basic["XF"], "hours": basic["XS"], "assessment_method_code": Text(basic["KSLXDM"]), "assessment_method": outlineDisplay(basic, "KSLXDM"), "grading_scale_code": Text(basic["CJJLFS"]), "grading_scale": outlineDisplay(basic, "CJJLFS"), "version": firstText(basic["BBWID"], basic["WID"]), "introduction": firstText(intro["KCJJ"], intro["KCJJYW"]), "course_nature": outlineDisplay(outline, "KCXZDM"), "applicable_majors": Text(outline["SYZY"]), "prerequisites": Text(outline["XXKC"]), "textbooks": firstText(outline["SYJC"], outline["JCXX"]), "failures": failures}, nil
}

func OutlineSearchSchema(ctx context.Context, requester Requester) (map[string]any, error) {
	fields, failed := []map[string]any{}, []string{}
	for _, spec := range []struct{ key, label, id string }{{"KKDWDM", "开课单位", "8afd75f4-fb19-4120-9d49-2e6e1de99f8f"}, {"KCCCDM", "课程层次", "ebb0e845-6ae0-44ab-aa5d-4b9f57ed1a1d"}, {"KCJBDM", "课程级别", "41507e52-6b2f-4e5e-b5de-b65bba206dcf"}} {
		options, seen := []map[string]string{}, map[string]bool{}
		encoded, err := requester.Request(ctx, http.MethodGet, "https://jwxt.neu.edu.cn/jwapp/code/"+spec.id+".do", nil)
		var payload any
		if err == nil {
			err = json.Unmarshal(encoded, &payload)
		}
		if err != nil {
			failed = append(failed, spec.key)
		} else {
			var visit func(any)
			visit = func(value any) {
				switch typed := value.(type) {
				case map[string]any:
					code := strings.TrimSpace(firstText(typed["value"], typed["code"], typed["id"], typed["DM"], typed["dm"], typed["key"], typed["KEY"]))
					label := ""
					for _, key := range []string{"label", "text", "name", "MC", "mc", "valueName", "display"} {
						if raw := typed[key]; raw != nil {
							switch raw.(type) {
							case string, float64:
								label = strings.TrimSpace(Text(raw))
							}
							if label != "" {
								break
							}
						}
					}
					if code != "" && label != "" && !seen[code] {
						seen[code] = true
						options = append(options, map[string]string{"value": code, "label": label})
					}
					for _, nested := range typed {
						visit(nested)
					}
				case []any:
					for _, nested := range typed {
						visit(nested)
					}
				}
			}
			visit(payload)
		}
		fields = append(fields, map[string]any{"key": spec.key, "label": spec.label, "type": "select", "enabled": len(options) > 0, "options": options})
	}
	fields = append(fields, map[string]any{"key": "XF", "label": "学分", "type": "range-preset", "enabled": true}, map[string]any{"key": "XS", "label": "学时", "type": "range-preset", "enabled": true})
	return map[string]any{"fields": fields, "partial": len(failed) > 0, "failed_fields": failed}, nil
}
