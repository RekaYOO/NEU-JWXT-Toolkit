package academic

import (
	"context"
	"errors"
	"net/url"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

type JWXKMutationResult struct {
	State                string `json:"state"`
	Success              bool   `json:"success"`
	Queued               bool   `json:"queued"`
	RequiresConfirmation bool   `json:"requires_confirmation"`
	Code                 string `json:"code"`
	Message              string `json:"message"`
	Failure              string `json:"failure_class"`
}

func uncertainJWXKMutation() JWXKMutationResult {
	return JWXKMutationResult{State: "unknown", Code: "unknown", Message: "学校操作结果尚未确认，请刷新核验；不会自动重复提交", Failure: "MUTATION_UNCERTAIN"}
}

func classifyJWXKMutation(code, message string) string {
	compact := strings.Join(strings.Fields(message), "")
	if code == "401" || code == "403" {
		return "AUTH_REQUIRED"
	}
	if code == "429" {
		return "RATE_LIMITED"
	}
	for _, group := range []struct {
		category string
		markers  []string
	}{
		{"AUTH_REQUIRED", []string{"登录失效", "重新登录", "身份认证", "未登录"}},
		{"RATE_LIMITED", []string{"请求过快", "操作频繁", "访问频繁", "稍后再试"}},
		{"UNAVAILABLE", []string{"已选课程超过选课模块门数或学分", "超过选课模块门数或学分", "不在选课范围", "当前不可选", "不能选择该教学班", "不允许选择", "不符合选课条件", "无选课资格"}},
		{"FULL", []string{"课程已满", "教学班已满", "容量已满", "人数已满", "没有余量", "无剩余名额"}},
		{"RACE_LOST", []string{"已被其他同学选中", "已被他人选中", "名额已被抢", "抢先失败"}},
	} {
		for _, marker := range group.markers {
			if strings.Contains(compact, marker) {
				return group.category
			}
		}
	}
	return "UNKNOWN"
}

func parseJWXKMutation(payload []byte) JWXKMutationResult {
	decoded, err := Decode(payload)
	if err != nil || Text(decoded["code"]) == "" {
		return uncertainJWXKMutation()
	}
	code, message := Text(decoded["code"]), firstText(decoded["msg"], decoded["message"])
	result := JWXKMutationResult{State: "unknown", Code: code, Message: message, Failure: "UNKNOWN"}
	if code == "301" {
		result.State, result.RequiresConfirmation, result.Failure = "rejected", true, ""
		if result.Message == "" {
			result.Message = "学校要求确认操作风险后再提交"
		}
		return result
	}
	if code == "200" {
		result.State, result.Success, result.Failure = "accepted", true, ""
		if result.Message == "" {
			result.Message = "学校已接收操作，正在核验最终结果"
		}
		for _, marker := range []string{"队列", "排队", "处理中", "等待处理", "等待确认"} {
			result.Queued = result.Queued || strings.Contains(message, marker)
		}
		return result
	}
	if strings.Contains(message, "已在选课结果中") {
		result.State, result.Failure = "accepted", ""
		result.Message = "学校提示课程已在结果中，需读取官方结果核验"
		return result
	}
	result.Failure = classifyJWXKMutation(code, message)
	if result.Failure != "UNKNOWN" {
		result.State = "rejected"
	}
	if result.Message == "" {
		result.Message = "学校未返回明确操作结果，请重新核验"
	}
	return result
}

func SendJWXKSelection(ctx context.Context, requester ServiceRequester, mode string, prepared PreparedJWXKSelection, confirmRisk bool) JWXKMutationResult {
	return sendJWXKMutation(ctx, requester, mode, prepared, confirmRisk)
}

func sendJWXKMutation(ctx context.Context, requester ServiceRequester, mode string, prepared PreparedJWXKSelection, confirmRisk bool) JWXKMutationResult {
	if prepared.path != "/xsxk/elective/clazz/add" && prepared.path != "/xsxk/elective/neu/clazz/weightAdd" && prepared.path != "/xsxk/elective/neu/clazz/del" || prepared.form.Get("secretVal") == "" || !selectionMutationScope(prepared.form.Get("clazzType")) {
		return JWXKMutationResult{State: "not_sent", Code: "invalid_preparation", Message: "选课提交材料无效，未发送请求", Failure: "UNAVAILABLE"}
	}
	form := url.Values{}
	for key, values := range prepared.form {
		form[key] = append([]string{}, values...)
	}
	for attempt := 0; attempt < 2; attempt++ {
		if ctx.Err() != nil {
			return JWXKMutationResult{State: "not_sent", Code: "cancelled", Message: "选课提交已取消，未发送请求"}
		}
		payload, err := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: prepared.path, Form: form})
		if err != nil {
			var failure *campus.Error
			if errors.As(err, &failure) && failure.Code == "SERVICE_NOT_SENT" {
				return JWXKMutationResult{State: "not_sent", Code: failure.Code, Message: "学校连接尚未建立，选课请求未发送", Failure: "AUTH_REQUIRED"}
			}
			return uncertainJWXKMutation()
		}
		result := parseJWXKMutation(payload)
		if prepared.path == "/xsxk/elective/neu/clazz/del" && result.State == "accepted" && result.Code != "200" {
			result = uncertainJWXKMutation()
		}
		if attempt == 0 && result.RequiresConfirmation && confirmRisk {
			form.Set("isConfirm", "1")
			continue
		}
		return result
	}
	return uncertainJWXKMutation()
}

func VerifyJWXKSelection(ctx context.Context, requester ServiceRequester, mode string, input JWXKSelect) (bool, string, error) {
	selected, err := FetchJWXKSelected(ctx, requester, mode, input.Batch)
	if err != nil {
		return false, "", err
	}
	term := Text(selected["term_code"])
	expectedMode := "02"
	if input.Weight != nil {
		expectedMode = "04"
	}
	if Text(selected["selection_type_code"]) != expectedMode {
		return false, term, nil
	}
	feed := "selected"
	if Text(selected["selection_type_code"]) == "04" {
		feed = "volunteered"
	}
	for _, course := range selected[feed].([]map[string]any) {
		if course["current_batch_record"] == true && Text(course["class_id"]) == input.Class && strings.EqualFold(Text(course["course_code"]), input.Course) {
			if input.Weight != nil && Number(course["devoted_weight"]) != float64(*input.Weight) {
				continue
			}
			return true, term, nil
		}
	}
	return false, term, nil
}
