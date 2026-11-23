package academic

import (
	"context"
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type ServiceRequester interface {
	RequestService(context.Context, campus.ServiceRequest) ([]byte, error)
}

var jwxkPublicBatches = regexp.MustCompile(`(?s)loginVue\.batchList\s*=\s*(\[.*?\])\s*;`)
var jwxkBatchCode = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var jwxkMenuNames = map[string]string{"TJKC": "任务推荐班课程", "FANKC": "培养方案内课", "FAWKC": "培养方案外课程", "XGKC": "通识选修课", "CXKC": "重修课程", "TYKC": "体育项目", "FXKC": "辅修课程", "ALLKC": "全校课程查询", "BYKC": "本研课程", "ZYNKC": "专业内课程"}

func jwxkData(ctx context.Context, requester ServiceRequester, mode, path string) (map[string]any, error) {
	payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: path, Form: url.Values{}, ReadOnly: true})
	if err != nil {
		return nil, err
	}
	decoded, err := Decode(payload)
	if err != nil {
		return nil, err
	}
	if Text(decoded["code"]) != "200" {
		return nil, &campus.Error{Code: "JWXK_BUSINESS_ERROR", Message: "学校选课接口暂时无法提供此项数据，请稍后重试"}
	}
	data := Object(decoded["data"])
	if data == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校选课数据结构异常"}
	}
	return data, nil
}

func jwxkBatch(row map[string]any, now time.Time) (map[string]any, error) {
	code := Text(row["code"])
	if !jwxkBatchCode.MatchString(code) || Text(row["name"]) == "" {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校选课轮次结构异常"}
	}
	state := "unknown"
	zone := time.FixedZone("Asia/Shanghai", 8*3600)
	begin, beginErr := time.ParseInLocation("2006-01-02 15:04:05", Text(row["beginTime"]), zone)
	end, endErr := time.ParseInLocation("2006-01-02 15:04:05", Text(row["endTime"]), zone)
	if !now.IsZero() && beginErr == nil && endErr == nil && !end.Before(begin) {
		state = "active"
		if now.Before(begin) {
			state = "not_started"
		}
		if now.After(end) {
			state = "ended"
		}
	}
	types := []string{}
	if row["clazzTypeList"] != nil {
		values, ok := row["clazzTypeList"].([]any)
		if !ok {
			return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "选课轮次课程类型结构异常"}
		}
		for _, value := range values {
			types = append(types, Text(value))
		}
	}
	menus := []map[string]string{}
	if row["menuList"] != nil {
		values, err := reportRows(row["menuList"])
		if err != nil {
			return nil, err
		}
		for _, menu := range values {
			if Text(menu["teachingClassType"]) != "" {
				kind := firstText(menu["code"], menu["teachingClassType"])
				name := firstText(menu["name"], menu["displayName"])
				if name == "" || name == kind {
					name = firstText(jwxkMenuNames[kind], "其他课程")
				}
				menus = append(menus, map[string]string{"code": kind, "name": name})
			}
		}
	}
	if len(menus) == 0 {
		for _, kind := range types {
			menus = append(menus, map[string]string{"code": kind, "name": firstText(jwxkMenuNames[kind], "其他课程")})
		}
	}
	selectable := Text(row["canSelect"]) == "1"
	return map[string]any{"code": code, "name": Text(row["name"]), "term_code": Text(row["schoolTerm"]), "term_name": Text(row["schoolTermName"]), "begin_time": Text(row["beginTime"]), "end_time": Text(row["endTime"]), "selection_type": Text(row["typeName"]), "selection_type_code": Text(row["typeCode"]), "tactic_name": Text(row["tacticName"]), "course_types": types, "need_confirm": Text(row["needConfirm"]) == "1", "notice": Text(row["confirmInfo"]), "state": state, "can_enter": selectable && state == "active", "access_scope": "account", "account_selectable": selectable, "confirmed": Text(row["isConfirmed"]) == "1", "week_range": Text(row["weekRange"]), "allow_conflict": Text(row["noCheckTimeConflict"]) == "1", "allow_cross_campus": Text(row["notRetakeMultiCampus"]) == "1", "menus": menus}, nil
}

func FetchJWXKContext(ctx context.Context, requester ServiceRequester, mode string) (map[string]any, error) {
	nowData, err := jwxkData(ctx, requester, mode, "/xsxk/web/now")
	if err != nil {
		return nil, err
	}
	studentData, err := jwxkData(ctx, requester, mode, "/xsxk/web/studentInfo")
	if err != nil {
		return nil, err
	}
	student := Object(studentData["student"])
	if student == nil {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校未返回选课账号资料"}
	}
	var now time.Time
	if timestamp, err := strconv.ParseInt(Text(nowData["currentTime"]), 10, 64); err == nil && timestamp > 0 {
		now = time.UnixMilli(timestamp)
	}
	rows := []map[string]any{}
	for _, field := range []string{"electiveBatchList", "expElectiveBatchList"} {
		items, err := reportRows(student[field])
		if err != nil {
			return nil, err
		}
		rows = append(rows, items...)
	}
	batches := []map[string]any{}
	seen := map[string]bool{}
	needsMenus := false
	for _, row := range rows {
		batch, err := jwxkBatch(row, now)
		if err != nil {
			return nil, err
		}
		code := Text(batch["code"])
		if seen[code] {
			continue
		}
		seen[code] = true
		if len(batch["menus"].([]map[string]string)) == 0 {
			needsMenus = true
		}
		batches = append(batches, batch)
	}
	if needsMenus {
		payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "GET", Path: "/xsxk/profile/index.html", ReadOnly: true})
		if err == nil {
			match := jwxkPublicBatches.FindSubmatch(payload)
			if len(match) > 1 {
				var public []map[string]any
				if json.Unmarshal(match[1], &public) == nil {
					for _, batch := range batches {
						if len(batch["menus"].([]map[string]string)) != 0 {
							continue
						}
						for _, row := range public {
							if Text(row["code"]) != Text(batch["code"]) {
								continue
							}
							if normalized, err := jwxkBatch(row, now); err == nil {
								batch["menus"], batch["course_types"] = normalized["menus"], normalized["course_types"]
							}
						}
					}
				}
			}
		}
	}
	campusCode, campusName := Text(student["campus"]), Text(student["campusName"])
	labels := map[string]string{"00": "南湖校区", "01": "浑南校区"}
	for code, label := range labels {
		if campusCode == label {
			campusCode = code
		}
	}
	if campusName == "" || campusName == campusCode {
		campusName = firstText(labels[campusCode], campusCode)
	}
	return map[string]any{"official_time": Text(nowData["currentTime"]), "online_count": nowData["onlineCount"], "current_campus": campusCode, "current_campus_name": campusName, "batches": batches}, nil
}
