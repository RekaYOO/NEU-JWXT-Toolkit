package application

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func legacyArchives(root *os.Root, account string) ([]map[string]any, error) {
	file, err := root.Open("course_selection_catalog_history.json")
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > store.MaxDocumentBatchBytes {
		return nil, errors.New("legacy course history is invalid or exceeds 128 MiB")
	}
	decoder := json.NewDecoder(io.LimitReader(file, store.MaxDocumentBatchBytes+1))
	decoder.UseNumber()
	token, err := decoder.Token()
	if err != nil || token != json.Delim('[') {
		return nil, errors.New("legacy course history must be an array")
	}
	result := []map[string]any{}
	count := 0
	for decoder.More() {
		count++
		if count > 10000 {
			return nil, errors.New("too many legacy course archives")
		}
		var raw json.RawMessage
		if decoder.Decode(&raw) != nil {
			return nil, errors.New("legacy course history is invalid")
		}
		var owner struct {
			Account string `json:"account"`
		}
		if json.Unmarshal(raw, &owner) != nil {
			return nil, errors.New("legacy archive owner is invalid")
		}
		if owner.Account != account {
			continue
		}
		if len(raw) > store.DocumentPayloadLimit(jwxkArchiveNamespace) {
			return nil, errors.New("legacy archive exceeds 64 MiB")
		}
		var archive map[string]any
		if json.Unmarshal(raw, &archive) != nil {
			return nil, errors.New("legacy archive is invalid")
		}
		converted, err := convertLegacyArchive(archive)
		if err != nil {
			return nil, err
		}
		result = append(result, converted)
	}
	if token, err := decoder.Token(); err != nil || token != json.Delim(']') || decoder.Decode(new(any)) != io.EOF {
		return nil, errors.New("legacy course history has invalid trailing content")
	}
	return result, nil
}

func convertLegacyArchive(archive map[string]any) (map[string]any, error) {
	batch, term := academic.Text(archive["batch_code"]), academic.Text(archive["term_code"])
	if !(academic.JWXKSearch{Batch: batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() || !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() {
		return nil, errors.New("legacy archive batch or term is invalid")
	}
	courses, ok := archive["courses"].([]any)
	if !ok || len(courses) > 50000 {
		return nil, errors.New("legacy archive course list is invalid")
	}
	converted := map[string]any{"archive_id": jwxkArchiveID(batch), "batch_code": batch, "term_code": term, "sync_status": "imported", "imported_from": "python", "sync_loaded": len(courses)}
	for _, field := range strings.Fields("batch_name term_name selection_type_code selection_type begin_time end_time archived created_at updated_at final_refresh_at final_refresh_status catalog_complete last_sync_at sync_total") {
		if value, exists := archive[field]; exists {
			converted[field] = value
		}
	}
	options := []map[string]string{}
	if archive["scope_options"] != nil {
		rawOptions, ok := archive["scope_options"].([]any)
		if !ok {
			return nil, errors.New("legacy archive scope options are invalid")
		}
		for _, value := range rawOptions {
			option, ok := value.(map[string]any)
			if !ok || academic.Text(option["code"]) == "" {
				return nil, errors.New("legacy archive scope option is invalid")
			}
			options = append(options, map[string]string{"code": academic.Text(option["code"]), "name": academic.Text(option["name"])})
		}
	}
	converted["scope_options"] = options
	rows := []map[string]any{}
	seen := map[string]bool{}
	fields := strings.Fields("course_code course_name class_id class_number credits hours teacher location official_schedule campus campus_name department course_nature course_category course_categories normalized_course_category general_elective_category_code general_elective_category exam_type_code exam_type score_scale_code score_scale teaching_mode teacher_details has_experiment experiment_hours experiment_schedules teacher_titles target_classes total_capacity capacity selected_count first_choice_count weight_participant_count devoted_weight selection_type_code market_participant_count market_participant_label market_capacity_label conflict conflict_description restricted eligibility_status eligibility_reason full selected course_already_selected has_test has_book notice schedules group_id source_tags teaching_class_type source_scopes capacity_updated_at")
	for _, raw := range courses {
		course, ok := raw.(map[string]any)
		class := academic.Text(course["class_id"])
		if !ok || class == "" || seen[class] {
			return nil, errors.New("legacy archive has invalid or duplicate teaching classes")
		}
		seen[class] = true
		row := map[string]any{}
		for _, field := range fields {
			if value, exists := course[field]; exists {
				row[field] = value
			}
		}
		for _, field := range []string{"schedules", "experiment_schedules"} {
			if row[field] == nil {
				row[field] = []any{}
			} else if _, ok := row[field].([]any); !ok {
				return nil, errors.New("legacy archive schedules are invalid")
			}
		}
		scopes := []string{}
		seenScopes := map[string]bool{}
		if rawScopes := row["source_scopes"]; rawScopes != nil {
			values, ok := rawScopes.([]any)
			if !ok {
				return nil, errors.New("legacy archive source scopes are invalid")
			}
			for _, value := range values {
				if text := academic.Text(value); text != "" && !seenScopes[text] {
					scopes = append(scopes, text)
					seenScopes[text] = true
				}
			}
		}
		currentScope := academic.Text(row["teaching_class_type"])
		if len(scopes) == 0 && currentScope != "" {
			scopes = append(scopes, currentScope)
		}
		if currentScope == "" || currentScope == "ALL" || currentScope == "ROUND" || currentScope == "ALLKC" {
			for _, scope := range scopes {
				if scope != "ALL" && scope != "ROUND" && scope != "ALLKC" {
					currentScope = scope
					break
				}
			}
		}
		if currentScope == "" {
			currentScope = "ALLKC"
		}
		row["source_scopes"], row["teaching_class_type"] = scopes, currentScope
		rows = append(rows, row)
	}
	converted["courses"] = rows
	return converted, nil
}
