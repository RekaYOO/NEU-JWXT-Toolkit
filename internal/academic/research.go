package academic

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"strconv"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

const researchBase = "https://jwxt.neu.edu.cn/jwapp/sys/kyxlneu/"

func researchInvalid() error {
	return &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "科研训练响应不完整，请稍后重试；已保留原有缓存"}
}

func researchData(ctx context.Context, requester Requester, group, action string, form url.Values) (any, error) {
	encoded, err := requester.Request(ctx, http.MethodPost, researchBase+"modules/"+group+"/"+action+".do", form)
	if err != nil {
		return nil, err
	}
	payload, err := Decode(encoded)
	if err != nil {
		return nil, err
	}
	if Text(payload["code"]) != "0" {
		return nil, &campus.Error{Code: "ACADEMIC_RESPONSE_REJECTED", Message: "学校未确认科研训练查询成功"}
	}
	value, exists := Object(payload["datas"])[action]
	if !exists || value == nil {
		return nil, researchInvalid()
	}
	return value, nil
}

func researchRows(value any) ([]map[string]any, error) {
	if container, ok := value.(map[string]any); ok {
		value = container["rows"]
	}
	if _, ok := value.([]any); !ok {
		return nil, researchInvalid()
	}
	return reportRows(value)
}

func researchPercent(value any) float64 {
	number := Number(value)
	if math.Abs(number) > 0 && math.Abs(number) <= 1 {
		number *= 100
	}
	return number
}

func FetchResearchBatch(ctx context.Context, requester Requester) (map[string]any, error) {
	value, err := researchData(ctx, requester, "kyxl", "cxdqxnxqpc", url.Values{})
	if err != nil {
		return nil, err
	}
	row := Object(value)
	if !ValidEvaluationID(Text(row["WID"])) {
		return nil, &campus.Error{Code: "ACADEMIC_RESPONSE_REJECTED", Message: "当前没有可用的科研训练报名批次"}
	}
	var minimum any
	if Text(row["XSJD"]) != "" {
		minimum = Number(row["XSJD"])
	}
	return map[string]any{
		"batch_id": Text(row["WID"]), "name": Text(row["PCMC"]), "term_code": Text(row["XNXQDM"]), "term_name": Text(row["XNXQDM_DISPLAY"]),
		"max_topics": int(Number(row["XSKXKTS"])), "rank_limit_percent": researchPercent(row["ZYPM"]), "minimum_gpa": minimum,
		"allow_failed_courses": Text(row["SFYXYBJGCJ"]) == "1", "allow_failed_courses_display": Text(row["SFYXYBJGCJ_DISPLAY"]), "minimum_journal_count": int(Number(row["ZBCS"])),
	}, nil
}

func FetchResearchEligibility(ctx context.Context, requester Requester, batch string) (map[string]any, error) {
	value, err := researchData(ctx, requester, "kyxl", "cxxsjdjzypm", url.Values{"PCWID": {batch}})
	if err != nil {
		return nil, err
	}
	rows, err := researchRows(value)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return map[string]any{"available": false, "gpa": nil, "major_rank": nil, "reason": "教务系统未返回平均绩点和专业排名，请联系教务老师补全科研训练资格数据"}, nil
	}
	rank := Text(rows[0]["ZYPM"])
	if number, err := strconv.ParseFloat(rank, 64); err == nil && !math.IsNaN(number) && !math.IsInf(number, 0) {
		rank = fmt.Sprintf("%.2f", researchPercent(rows[0]["ZYPM"]))
	}
	return map[string]any{"available": true, "gpa": Text(rows[0]["PJJD"]), "major_rank": rank, "reason": ""}, nil
}

func researchTopic(row map[string]any) map[string]any {
	result := map[string]any{}
	for output, fields := range map[string][]string{
		"topic_id": {"WID", "KTWID"}, "batch_id": {"PCWID"}, "title": {"YJTM"}, "project_name": {"KYXMMC"},
		"major": {"SSZY_DISPLAY", "SSZY"}, "college": {"SSYX_DISPLAY", "SSYX"}, "key_laboratory_name": {"SSZDSYS_DISPLAY", "SSZDSYS"},
		"advisor_id": {"SQR"}, "advisor_name": {"DSXM", "SQRXM"}, "advisor_college": {"DSDW_DISPLAY", "SSYX_DISPLAY", "DSDW"}, "contact": {"LXFS"},
		"registration_id": {"BMWID"}, "registration_status_code": {"BMZT"}, "registration_status": {"BMZT_DISPLAY", "BMZT"}, "rejection_reason": {"THLY"}, "application_reason": {"SQLY"},
	} {
		result[output] = ""
		for _, field := range fields {
			if text := Text(row[field]); text != "" {
				result[output] = text
				break
			}
		}
	}
	capacity, registered := int(Number(row["ZSXSS"])), int(Number(row["YBMRS"]))
	selected := Text(row["BMWID"]) != ""
	full := capacity > 0 && registered >= capacity
	result["capacity"], result["registered_count"], result["confirmed_count"] = capacity, registered, int(Number(row["YQRRS"]))
	result["key_laboratory"], result["is_registered"], result["is_full"] = Text(row["SFZDSYS"]) == "1", selected, full
	result["can_enroll"], result["can_cancel"] = !selected && !full, Text(row["BMZT"]) == "-10" || Text(row["BMZT"]) == "10"
	return result
}

func researchRule(field, value string) string {
	encoded, _ := json.Marshal([]map[string]string{{"name": field, "builder": "equal", "linkOpt": "AND", "value": value}})
	return string(encoded)
}

func fetchResearchPages(ctx context.Context, requester Requester, batch, action, identifier string) ([]map[string]any, error) {
	result := []map[string]any{}
	seen := map[string]bool{}
	expected := -1
	for page := 1; page <= 200; page++ {
		value, err := researchData(ctx, requester, "kyxl", action, url.Values{"querySetting": {researchRule("PCWID", batch)}, "pageSize": {"50"}, "pageNumber": {strconv.Itoa(page)}})
		if err != nil {
			return nil, err
		}
		rows, err := researchRows(value)
		if err != nil {
			return nil, err
		}
		container := Object(value)
		total := len(rows)
		if raw, exists := container["totalSize"]; exists {
			parsed, parseErr := strconv.Atoi(Text(raw))
			if parseErr != nil || parsed < 0 || parsed > 10000 {
				return nil, researchInvalid()
			}
			total = parsed
		} else if action == "ktbmcxlb" || len(rows) >= 50 {
			return nil, researchInvalid()
		}
		if expected >= 0 && expected != total {
			return nil, researchInvalid()
		}
		expected = total
		for _, row := range rows {
			key := Text(row[identifier])
			if key == "" && action == "ktbmcxlb" {
				key = Text(row["KTWID"])
			}
			if !ValidEvaluationID(key) || seen[key] || (Text(row["PCWID"]) != "" && Text(row["PCWID"]) != batch) {
				return nil, researchInvalid()
			}
			seen[key] = true
			result = append(result, row)
		}
		if len(result) == expected {
			return result, nil
		}
		if len(result) > expected || len(rows) == 0 {
			return nil, researchInvalid()
		}
	}
	return nil, researchInvalid()
}

func FetchResearchTopics(ctx context.Context, requester Requester, batch string) ([]map[string]any, error) {
	rows, err := fetchResearchPages(ctx, requester, batch, "ktbmcxlb", "WID")
	if err != nil {
		return nil, err
	}
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		result = append(result, researchTopic(row))
	}
	return result, nil
}

func FetchResearchTopicDetail(ctx context.Context, requester Requester, identifier string) (map[string]any, error) {
	value, err := researchData(ctx, requester, "jsktgl", "ktsbglcxlb", url.Values{"WID": {identifier}})
	if err != nil {
		return nil, err
	}
	rows, err := researchRows(value)
	if err != nil || len(rows) != 1 || Text(rows[0]["WID"]) != identifier {
		return nil, researchInvalid()
	}
	row := rows[0]
	result := researchTopic(row)
	result["introduction"], result["requirements"] = Text(row["KTJJ"]), Text(row["BMYQ"])
	if result["requirements"] == "" {
		result["requirements"] = Text(row["XSBMTJ"])
	}
	result["advisor_email"], result["advisor_title"] = Text(row["DZYX"]), Text(row["ZC_DISPLAY"])
	if result["advisor_title"] == "" {
		result["advisor_title"] = Text(row["ZC"])
	}
	return result, nil
}

func FetchResearchConfirmed(ctx context.Context, requester Requester, batch string) ([]map[string]any, error) {
	rows, err := fetchResearchPages(ctx, requester, batch, "yqrktcxlb", "WID")
	if err != nil {
		return nil, err
	}
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		topic := researchTopic(row)
		item := map[string]any{}
		for _, key := range []string{"title", "project_name", "major", "college", "contact", "key_laboratory", "key_laboratory_name"} {
			item[key] = topic[key]
		}
		for output, fields := range map[string][]string{
			"record_id": {"WID"}, "topic_id": {"KTWID"}, "batch_id": {"PCWID"}, "batch_name": {"PCWID_DISPLAY"},
			"advisor_name": {"SQRXM", "DSXM"}, "advisor_contact": {"LXFS"}, "advisor_email": {"DZYX"}, "advisor_title": {"ZC_DISPLAY", "ZC"},
			"score": {"CJ"}, "introduction": {"KTJJ"}, "requirements": {"BMYQ", "XSBMTJ"}, "department": {"SZDWDM_DISPLAY", "SZDWDM"}, "confirmed_status": {"QRZT_DISPLAY", "QRZT"},
		} {
			item[output] = ""
			for _, field := range fields {
				if text := Text(row[field]); text != "" {
					item[output] = text
					break
				}
			}
		}
		item["journal_count"] = int(Number(row["ZJS"]))
		attachment := row["XXZJFJ"]
		item["attachment"], item["has_attachment"] = "", false
		switch value := attachment.(type) {
		case map[string]any:
			item["attachment"], item["has_attachment"] = value, len(value) > 0
		case []any:
			item["attachment"], item["has_attachment"] = value, len(value) > 0
		default:
			item["attachment"], item["has_attachment"] = Text(value), Text(value) != ""
		}
		result = append(result, item)
	}
	return result, nil
}

func FetchResearchJournals(ctx context.Context, requester Requester, identifier string) ([]map[string]any, error) {
	if !ValidEvaluationID(identifier) {
		return nil, researchInvalid()
	}
	value, err := researchData(ctx, requester, "kyxl", "xszjcxlb", url.Values{"BMWID": {identifier}})
	if err != nil {
		return nil, err
	}
	rows, err := researchRows(value)
	if err != nil {
		return nil, err
	}
	result := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		if Text(row["BMWID"]) != "" && Text(row["BMWID"]) != identifier {
			return nil, researchInvalid()
		}
		item := map[string]any{"confirmed_record_id": identifier, "sort": row["PX"]}
		for output, field := range map[string]string{
			"record_id": "WID", "content": "XLNR", "start_at": "KYXLKSSJ", "end_at": "KYXLJSSJ",
			"mentor_start_at": "JSZDKSSJ", "mentor_end_at": "JSZDJSSJ", "status_code": "PYZT", "status": "PYZT_DISPLAY", "attachment": "FJ",
		} {
			item[output] = Text(row[field])
		}
		if item["status"] == "" {
			item["status"] = item["status_code"]
		}
		result = append(result, item)
	}
	return result, nil
}

func FetchResearchSnapshot(ctx context.Context, requester Requester) (map[string]any, error) {
	batch, err := FetchResearchBatch(ctx, requester)
	if err != nil {
		return nil, err
	}
	identifier := Text(batch["batch_id"])
	eligibility, err := FetchResearchEligibility(ctx, requester, identifier)
	if err != nil {
		return nil, err
	}
	topics, err := FetchResearchTopics(ctx, requester, identifier)
	if err != nil {
		return nil, err
	}
	confirmed, err := FetchResearchConfirmed(ctx, requester, identifier)
	if err != nil {
		return nil, err
	}
	latest, err := FetchResearchBatch(ctx, requester)
	if err != nil {
		return nil, err
	}
	if Text(latest["batch_id"]) != identifier {
		return nil, researchInvalid()
	}
	return map[string]any{"batch": batch, "eligibility": eligibility, "topics": topics, "confirmed_topics": confirmed, "total": len(topics)}, nil
}
