package academic

import (
	"context"
	"net/url"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
)

func ClassifyJWXKRecord(record map[string]any, mode, batch, term, feed string) {
	participant, currentFeed := "selected_count", "selected"
	if mode == "04" {
		participant, currentFeed = "weight_participant_count", "volunteered"
	}
	reason := ""
	switch {
	case record[participant] != nil && record["capacity"] != nil && Number(record[participant]) == 0 && Number(record["capacity"]) == 0:
		reason = "人数和容量均为 0，属于其他轮次或不可操作记录"
	case feed != "" && feed != currentFeed:
		reason = "结果来源不属于当前轮次类型"
	case record["official_operation_available"] == true:
	case Text(record["record_batch_code"]) != "" && batch != "" && Text(record["record_batch_code"]) != batch:
		reason = "记录属于其他选课批次"
	case Text(record["record_term_code"]) != "" && term != "" && Text(record["record_term_code"]) != term:
		reason = "记录属于其他学期"
	}
	record["current_batch_record"], record["operation_allowed"] = reason == "", reason == ""
	record["operation_block_reason"], record["selection_record_type"] = reason, feed
}

func FetchJWXKSelected(ctx context.Context, requester ServiceRequester, mode, batchCode string) (map[string]any, error) {
	if !jwxkBatchCode.MatchString(batchCode) {
		return nil, &campus.Error{Code: "INVALID_REQUEST", Message: "选课轮次无效"}
	}
	batch, err := activateJWXKBatch(ctx, requester, mode, batchCode, false)
	if err != nil {
		return nil, err
	}
	result := map[string]any{"selected": []map[string]any{}, "volunteered": []map[string]any{}, "withdrawal": []map[string]any{}}
	failed := []string{}
	var firstError error
	for _, feed := range []struct{ name, target, source, path string }{
		{"selected", "selected", "yxkcyx", "/xsxk/elective/select"},
		{"volunteered", "volunteered", "fakcyx", "/xsxk/volunteer/select"},
		{"general_volunteered", "volunteered", "xgxkyx", "/xsxk/volunteer/xgxk/select"},
		{"withdrawal", "withdrawal", "", "/xsxk/elective/neu/deselect"},
	} {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		payload, fetchError := requester.RequestService(ctx, campus.ServiceRequest{Service: "jwxk", Mode: mode, Method: "POST", Path: feed.path, Form: url.Values{}, PreserveSessionOnAuth: true})
		var rows []map[string]any
		if fetchError == nil {
			var decoded map[string]any
			decoded, fetchError = jwxkPayload(payload)
			if fetchError == nil {
				if _, exists := decoded["data"]; !exists {
					fetchError = &campus.Error{Code: "UPSTREAM_RESPONSE_INVALID", Message: "学校未返回选课结果数据"}
				} else {
					rows, fetchError = reportRows(decoded["data"])
				}
			}
		}
		var courses []map[string]any
		if fetchError == nil {
			tagged := make([]any, 0, len(rows))
			for _, row := range rows {
				row["_selection_source"] = feed.source
				row["_official_operation_available"] = feed.source != "" && Text(row["secretVal"]) != ""
				tagged = append(tagged, row)
			}
			courses, fetchError = NormalizeJWXKCourses(tagged)
		}
		if fetchError != nil {
			if firstError == nil {
				firstError = fetchError
			}
			failed = append(failed, feed.name)
			continue
		}
		for _, course := range courses {
			ClassifyJWXKRecord(course, Text(batch["typeCode"]), batchCode, Text(batch["schoolTerm"]), feed.target)
			result[feed.target] = append(result[feed.target].([]map[string]any), course)
		}
	}
	if len(failed) == 4 {
		return nil, firstError
	}
	result["complete"], result["unavailable_feeds"] = len(failed) == 0, failed
	result["selection_type_code"], result["term_code"] = Text(batch["typeCode"]), Text(batch["schoolTerm"])
	return result, nil
}

func EnrichJWXKSelected(result map[string]any, live map[string]map[string]any, batch string) {
	fields := []string{"total_capacity", "capacity", "selected_count", "first_choice_count", "weight_participant_count", "market_participant_count", "market_participant_label", "market_capacity_label", "full", "schedules", "location", "campus", "campus_name", "teacher", "teaching_class_type", "course_nature", "course_category", "normalized_course_category", "course_categories", "general_elective_category", "general_elective_category_code"}
	for _, feed := range []string{"selected", "volunteered", "withdrawal"} {
		for _, course := range result[feed].([]map[string]any) {
			if current := live[Text(course["class_id"])]; current != nil {
				for _, field := range fields {
					if value, exists := current[field]; exists {
						course[field] = value
					}
				}
			}
			ClassifyJWXKRecord(course, Text(result["selection_type_code"]), batch, Text(result["term_code"]), feed)
		}
	}
}
