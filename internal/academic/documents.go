package academic

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

const documentBase = "https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/"

type AcademicDocument struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Category string `json:"category"`
	Limit    *int   `json:"semester_limit"`
}

func academicDocumentRows(value any) ([]map[string]any, error) {
	if _, ok := value.([]any); !ok {
		return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校证明清单结构异常"}
	}
	return reportRows(value)
}

func FetchAcademicDocuments(ctx context.Context, requester Requester) ([]AcademicDocument, error) {
	if _, err := requester.Request(ctx, http.MethodGet, documentBase+"*default/index.do?THEME=golden&forceApp=zmdyneu", nil); err != nil {
		return nil, err
	}
	encoded, err := requester.Request(ctx, http.MethodPost, documentBase+"zmdy/queryAllZmdy.do", url.Values{})
	if err != nil {
		return nil, err
	}
	payload, err := Decode(encoded)
	if err != nil {
		return nil, err
	}
	if payload["success"] != true {
		return nil, &campus.Error{Code: "ACADEMIC_DOCUMENT_REJECTED", Message: "学校暂时无法提供可打印证明清单"}
	}
	groups, err := academicDocumentRows(payload["datas"])
	if err != nil {
		return nil, err
	}
	result := []AcademicDocument{}
	seen := map[string]bool{}
	for _, group := range groups {
		category := strings.TrimSpace(Text(group["CDMC"]))
		if category == "" {
			category = "其他证明"
		}
		rows, err := academicDocumentRows(group["REPORTS"])
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			identifier, name := strings.TrimSpace(Text(row["WID"])), strings.TrimSpace(Text(row["CDMC"]))
			if identifier == "" || name == "" {
				continue
			}
			if !ValidEvaluationID(identifier) || seen[identifier] {
				return nil, &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校证明清单标识异常"}
			}
			seen[identifier] = true
			var limit *int
			if number, err := strconv.Atoi(Text(row["XNXQWD_DBDX"])); err == nil && number > 0 {
				limit = &number
			}
			result = append(result, AcademicDocument{ID: identifier, Name: name, Category: category, Limit: limit})
		}
	}
	return result, nil
}

func PreflightAcademicDocument(ctx context.Context, requester Requester, identifier string) (AcademicDocument, error) {
	documents, err := FetchAcademicDocuments(ctx, requester)
	if err != nil {
		return AcademicDocument{}, err
	}
	var document AcademicDocument
	for _, item := range documents {
		if item.ID == identifier {
			document = item
			break
		}
	}
	if document.ID == "" {
		return document, &campus.Error{Code: "ACADEMIC_DOCUMENT_REJECTED", Message: "该证明当前不可用，请刷新清单后重试"}
	}
	encoded, err := requester.Request(ctx, http.MethodPost, documentBase+"zmdy/printBefore.do", url.Values{"wid": {identifier}})
	if err != nil {
		return document, err
	}
	payload, err := Decode(encoded)
	if err != nil {
		return document, err
	}
	if payload["success"] != true {
		return document, &campus.Error{Code: "ACADEMIC_DOCUMENT_REJECTED", Message: "学校未通过证明打印预检，请检查权限和打印次数"}
	}
	if Text(payload["fwlx"]) == "2" {
		return document, &campus.Error{Code: "ACADEMIC_DOCUMENT_REJECTED", Message: "该证明需要在教务系统提交申请，暂不支持直接生成"}
	}
	if payload["hasQx"] != true {
		return document, &campus.Error{Code: "ACADEMIC_DOCUMENT_REJECTED", Message: "当前无权打印该证明或已达打印上限，请联系学院教学办"}
	}
	return document, nil
}
