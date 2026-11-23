package academic

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

type ResearchEnrollment struct {
	Topic  string `json:"topic_id"`
	Batch  string `json:"batch_id"`
	Phone  string `json:"phone"`
	Email  string `json:"email"`
	Reason string `json:"reason"`
}

func (input ResearchEnrollment) Valid() bool {
	if !ValidEvaluationID(input.Topic) || !ValidEvaluationID(input.Batch) {
		return false
	}
	for _, field := range []struct {
		value            string
		minimum, maximum int
	}{{input.Phone, 1, 16}, {input.Email, 3, 20}, {input.Reason, 0, 300}} {
		length := utf8.RuneCountInString(strings.TrimSpace(field.value))
		if length < field.minimum || length > field.maximum {
			return false
		}
		for _, character := range field.value {
			if unicode.IsControl(character) && character != '\n' && character != '\t' {
				return false
			}
		}
	}
	return true
}

func SendResearchEnrollment(ctx context.Context, requester Requester, input ResearchEnrollment, eligibility map[string]any) (string, string) {
	if !input.Valid() || eligibility["available"] != true || strings.TrimSpace(Text(eligibility["gpa"])) == "" || strings.TrimSpace(Text(eligibility["major_rank"])) == "" {
		return "not_sent", "报名参数或学校资格信息不完整，未提交报名"
	}
	form, _ := json.Marshal(map[string]string{"LXDH": strings.TrimSpace(input.Phone), "DZYX": strings.TrimSpace(input.Email), "PJJD": Text(eligibility["gpa"]), "PM": Text(eligibility["major_rank"]), "SQLY": strings.TrimSpace(input.Reason)})
	return sendResearchMutation(ctx, requester, "save", url.Values{"FORMJSON": {string(form)}, "KTWID": {input.Topic}})
}

func SendResearchCancellation(ctx context.Context, requester Requester, topic string) (string, string) {
	if !ValidEvaluationID(topic) {
		return "not_sent", "课题编号无效，未提交取消请求"
	}
	return sendResearchMutation(ctx, requester, "qxbm", url.Values{"querySetting": {researchRule("WID", topic)}})
}

func sendResearchMutation(ctx context.Context, requester Requester, action string, form url.Values) (string, string) {
	encoded, err := requester.Request(ctx, http.MethodPost, researchBase+"api/kyxl/ktbm/"+action+".do", form)
	if err != nil {
		return "unknown", "学校操作结果尚未确认，请核验报名记录；不会重复提交"
	}
	payload, err := Decode(encoded)
	if err != nil || payload["code"] == nil {
		return "unknown", "学校响应未能确认操作结果，请核验报名记录"
	}
	if _, err := strconv.ParseInt(Text(payload["code"]), 10, 64); err != nil {
		return "unknown", "学校响应未能确认操作结果，请核验报名记录"
	}
	if Text(payload["code"]) != "0" {
		return "rejected", "学校未接受本次操作，请刷新资格和报名状态后重试"
	}
	return "confirmed", "学校已确认操作成功"
}
