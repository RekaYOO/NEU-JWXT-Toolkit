package application

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) exportReport(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var normalized, original map[string]any
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var fetchErr error
		normalized, original, fetchErr = academic.FetchReportForExport(request.Context(), runtime, session.Account)
		return fetchErr
	})
	if err != nil {
		api.resourceError(response, err)
		return
	}
	payload, err := reportArchive(normalized, original)
	if err != nil {
		api.resourceError(response, err)
		return
	}
	current, err := api.session(request)
	if err != nil || current.Account != session.Account || current.Generation != session.Generation {
		api.resourceError(response, store.ErrAccess)
		return
	}
	response.Header().Set("Content-Type", "application/zip")
	response.Header().Set("Content-Disposition", `attachment; filename="academic-report.zip"`)
	response.Header().Set("Cache-Control", "no-store")
	response.Write(payload)
}

type reportColumn struct{ key, label string }

func reportArchive(report, original map[string]any) ([]byte, error) {
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	addCSV := func(name string, columns []reportColumn, rows []map[string]any) error {
		file, err := archive.Create(name)
		if err != nil {
			return err
		}
		if _, err = file.Write([]byte{0xef, 0xbb, 0xbf}); err != nil {
			return err
		}
		writer := csv.NewWriter(file)
		writer.UseCRLF = true
		record := make([]string, len(columns))
		for index, col := range columns {
			record[index] = col.label
		}
		if err = writer.Write(record); err != nil {
			return err
		}
		for _, row := range rows {
			for index, col := range columns {
				value := row[col.key]
				text := ""
				if value != nil {
					text = fmt.Sprint(value)
				}
				if _, ok := value.(string); ok {
					trimmed := strings.TrimSpace(text)
					if strings.HasPrefix(text, "\t") || strings.HasPrefix(text, "\r") || strings.HasPrefix(text, "\n") || (trimmed != "" && strings.ContainsRune("=+-@", rune(trimmed[0]))) {
						text = "'" + text
					}
				}
				record[index] = text
			}
			if err = writer.Write(record); err != nil {
				return err
			}
		}
		writer.Flush()
		return writer.Error()
	}
	basic := map[string]any{}
	for key, value := range report {
		basic[key] = value
	}
	summary, _ := report["credit_summary"].(map[string]any)
	for key, value := range summary {
		basic[key] = value
	}
	if err := addCSV("academic_report_basic.csv", []reportColumn{{"student_name", "姓名"}, {"student_id", "学号"}, {"grade", "年级"}, {"college", "学院"}, {"major", "专业"}, {"class_name", "班级"}, {"expected_graduation", "预计毕业日期"}, {"program_name", "培养方案名称"}, {"program_code", "培养方案代码"}, {"total_required", "要求学分"}, {"total_passed", "已修学分"}, {"total_selected", "已选学分"}, {"calculated_time", "计算时间"}}, []map[string]any{basic}); err != nil {
		return nil, err
	}
	var categories, courses []map[string]any
	var walk func([]map[string]any, string)
	walk = func(nodes []map[string]any, parent string) {
		for _, node := range nodes {
			category := map[string]any{}
			for key, value := range node {
				category[key] = value
			}
			category["parent"] = parent
			categories = append(categories, category)
			items, _ := node["courses"].([]map[string]any)
			courses = append(courses, items...)
			children, _ := node["children"].([]map[string]any)
			name, _ := node["name"].(string)
			walk(children, name)
		}
	}
	nodes, _ := report["categories"].([]map[string]any)
	walk(nodes, "")
	courseColumns := []reportColumn{{"category_path", "类别路径"}, {"course_name", "课程名称"}, {"course_code", "课程代码"}, {"credit", "学分"}, {"score", "成绩"}, {"is_passed", "是否通过"}, {"status", "修读状态"}, {"term_code", "学期"}, {"select_term_code", "选课学期"}, {"score_term_code", "成绩学期"}, {"plan_term_code", "计划学期"}, {"retake_status", "重修重考"}, {"course_nature", "课程性质"}, {"exam_type", "考核方式"}, {"dept_code", "开课单位"}, {"substitute_course_name", "替代课程"}, {"substitute_credit", "替代学分"}}
	if len(categories) > 0 {
		if err := addCSV("academic_report_categories.csv", []reportColumn{{"name", "类别名称"}, {"parent", "父类别"}, {"required_credits", "要求学分"}, {"passed_credits", "已修学分"}, {"selected_credits", "已选学分"}, {"remaining_credits", "剩余学分"}, {"is_completed", "是否满足"}}, categories); err != nil {
			return nil, err
		}
	}
	if len(courses) > 0 {
		if err := addCSV("academic_report_courses.csv", courseColumns, courses); err != nil {
			return nil, err
		}
	}
	if rows, _ := report["outside_courses"].([]map[string]any); len(rows) > 0 {
		if err := addCSV("academic_report_outside.csv", courseColumns, rows); err != nil {
			return nil, err
		}
	}
	file, err := archive.Create("academic_report_full.json")
	if err != nil {
		return nil, err
	}
	if err = json.NewEncoder(file).Encode(original); err != nil {
		return nil, err
	}
	if err = archive.Close(); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}
