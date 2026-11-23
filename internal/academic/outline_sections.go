package academic

import (
	"context"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type outlineSection struct {
	key, action, title string
	labels             [][2]string
}

var outlineSections = map[string][]outlineSection{
	"teaching": {
		{"outline", "cxkcdgxx", "课程与教材信息", [][2]string{{"SYZY", "适用专业"}, {"SFXYJC_DISPLAY", "选用教材"}, {"XXKC", "先修课程"}, {"CKSJJXZY", "参考书及学习资源"}, {"SYJC", "使用教材"}, {"KCXZDM_DISPLAY", "课程性质"}, {"QTSM", "其他说明"}}},
		{"objectives", "cxkcmbxx", "课程目标", [][2]string{{"KCMB", "课程目标"}}},
		{"objective_support", "kcmbybyzccx", "毕业要求支撑", [][2]string{{"KCMB", "课程目标"}, {"BYYQ", "毕业要求指标点"}, {"CD_DISPLAY", "支撑强度"}, {"QZ", "权重"}}},
		{"objective_content", "cxkcmbhnrdgx", "教学安排", [][2]string{{"ZJ", "章节/知识模块"}, {"JXNR", "教学内容"}, {"JXYQ", "教学要求"}, {"JXFF", "教学方法"}, {"DYKCMB", "对应课程目标"}, {"KTJSXS", "课堂讲授学时"}, {"SYXS", "实验学时"}, {"KCSJXS", "课程设计学时"}, {"SJXS", "实践学时"}, {"TLXS", "讨论学时"}, {"SJIXS", "上机学时"}}},
		{"attainment", "cxkcmbdcbz", "课程目标达成标准", [][2]string{{"DCQKPJDJ_DISPLAY", "达成等级"}, {"KCMBDCQKPJBZ", "课程目标达成标准"}, {"PJDJFS", "评定分数范围"}}},
	},
	"assessment": {
		{"score_formula", "kccjlrgs", "", nil},
		{"grading_method", "cxkccjpdff", "成绩评定方法", [][2]string{{"KCCJPDFF", "成绩评定方法"}}},
		{"assessment_types", "cxkhxs", "", nil}, {"assessment_links", "cxkhhjsz", "", nil}, {"assessment_weights", "cxkhxscjzb", "", nil},
	},
	"governance": {
		{"quality_improvement", "cxkczlpjhgjjz", "质量评价与持续改进", [][2]string{{"KCZLPJHGJJZ", "课程质量评价与持续改进机制"}}},
		{"authors", "cxzbrxgxx", "编制信息", [][2]string{{"ZBR", "执笔人"}, {"SHR", "审核人"}, {"PZR", "批准人"}, {"BZRQ", "编制日期"}}},
		{"attachments", "cxkcdgfj", "历史附件", nil},
	},
}

func ValidOutlineGroup(group string) bool { _, ok := outlineSections[group]; return ok }

func OutlineSections(ctx context.Context, requester Requester, code, group string) (map[string]any, error) {
	if !ValidCourseCode(code) || !ValidOutlineGroup(group) {
		return nil, &campus.Error{Code: "INVALID_OUTLINE_QUERY", Message: "大纲课程或分组无效"}
	}
	raw, failures := map[string][]map[string]any{}, map[string]string{}
	for _, section := range outlineSections[group] {
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
		raw[section.key] = rows
	}
	sections := []map[string]any{}
	for _, section := range outlineSections[group] {
		if section.key == "attachments" {
			items := []map[string]any{}
			for _, row := range raw[section.key] {
				if name := Text(row["FJ"]); name != "" {
					item := map[string]any{"name": name}
					for _, key := range []string{"FJID", "FJBH", "WID", "ATTACHMENT_ID", "FILE_ID", "TOKEN"} {
						if token := Text(row[key]); token != "" { item["token"] = token; break }
					}
					items = append(items, item)
				}
			}
			if len(items) > 0 {
				sections = append(sections, map[string]any{"kind": "attachments", "title": section.title, "items": items})
			}
			continue
		}
		items := [][]map[string]string{}
		for _, row := range raw[section.key] {
			item := []map[string]string{}
			for _, label := range section.labels {
				if value := outlineDisplay(row, label[0]); value != "" {
					item = append(item, map[string]string{"label": label[1], "value": value})
				}
			}
			if section.key == "objective_content" {
				hours := []string{}
				for index := 1; index <= 8; index++ {
					if value := Text(row["QTXS"+strconv.Itoa(index)]); value != "" && value != "0" {
						hours = append(hours, value)
					}
				}
				if len(hours) > 0 {
					item = append(item, map[string]string{"label": "其他学时", "value": strings.Join(hours, " / ")})
				}
			}
			if len(item) > 0 {
				items = append(items, item)
			}
		}
		if len(items) > 0 {
			kind := "info"
			if len(items) > 1 {
				kind = "cards"
			}
			sections = append(sections, map[string]any{"kind": kind, "title": section.title, "items": items})
		}
	}
	if group == "assessment" {
		types, links := map[string]string{}, map[string]string{}
		for _, row := range raw["assessment_types"] {
			types[Text(row["WID"])] = outlineDisplay(row, "KHXSMC")
		}
		for _, row := range raw["assessment_links"] {
			links[Text(row["WID"])] = outlineDisplay(row, "KCMB")
		}
		weights := raw["assessment_weights"]
		sort.SliceStable(weights, func(left, right int) bool { return Text(weights[left]["PX"]) < Text(weights[right]["PX"]) })
		rows := []map[string]any{}
		for _, row := range weights {
			target, method, weight := links[Text(row["KHHJWID"])], types[Text(row["KHXSWID"])], row["CJZB"]
			if target == "" && method == "" && Text(weight) == "" {
				continue
			}
			if Text(weight) == "" {
				weight = "—"
			}
			rows = append(rows, map[string]any{"课程目标": firstText(target, "—"), "考核形式": firstText(method, "—"), "成绩占比": weight})
		}
		if len(rows) > 0 {
			sections = append(sections, map[string]any{"kind": "table", "title": "考核矩阵", "columns": []string{"课程目标", "考核形式", "成绩占比"}, "rows": rows})
		}
	}
	return map[string]any{"course_code": code, "group": group, "sections": sections, "failures": failures}, nil
}
