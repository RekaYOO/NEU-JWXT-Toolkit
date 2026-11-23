package application

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) saveJWXKScan(ctx context.Context, session store.Session, batch string, document store.Document, archive map[string]any) error {
	if document.Deleted || archive == nil {
		return store.ErrMissing
	}
	archive["updated_at"] = time.Now().UTC().Format(time.RFC3339)
	payload, err := json.Marshal(archive)
	if err != nil {
		return err
	}
	_, err = api.database.SaveForSession(ctx, session, jwxkArchiveNamespace, jwxkArchiveID(batch), payload, document.Revision, false)
	return err
}

func (api *API) scheduleJWXKScan(ctx context.Context, session store.Session, batch, mode string) (string, error) {
	document, archive, err := api.readJWXKArchive(ctx, session, batch)
	if err != nil || archive == nil || document.Deleted || archive["archived"] == true {
		return "", err
	}
	lastSync, _ := time.Parse(time.RFC3339, academic.Text(archive["last_sync_at"]))
	if archive["catalog_complete"] == true && time.Since(lastSync) < 10*time.Minute {
		return "", nil
	}
	retry, _ := time.Parse(time.RFC3339, academic.Text(archive["sync_retry_at"]))
	if time.Now().Before(retry) {
		return "", nil
	}
	id, err := api.resources.startDocumentWork(session, "jwxk-catalog", batch, func(ctx context.Context, runtime *Runtime) error {
		var err error
		for attempt := 0; attempt < 3; attempt++ {
			if attempt > 0 {
				timer := time.NewTimer(time.Duration(attempt) * time.Minute)
				select {
				case <-timer.C:
				case <-ctx.Done():
					timer.Stop()
					return ctx.Err()
				}
			}
			err = api.scanJWXKCatalog(ctx, session, runtime, batch, mode)
			if err == nil || ctx.Err() != nil || errors.Is(err, store.ErrMissing) || errors.Is(err, store.ErrAccess) || errors.Is(err, store.ErrConflict) {
				return err
			}
			var failure *campus.Error
			if errors.As(err, &failure) && (failure.Code == "JWXK_BATCH_MISSING" || failure.Code == "SERVICE_AUTH_REQUIRED" || failure.Code == "AUTH_REQUIRED") {
				return err
			}
		}
		return err
	})
	return id, err
}

func (api *API) resumeAccountWork(session store.Session) {
	api.wakeGradeTracking(session)
	api.warmCachedScoreDetails(session)
	runtime := api.accounts.Runtime(session)
	if runtime == nil || runtime.Context.Err() != nil {
		return
	}
	mode, _, err := api.jwxkPreference(runtime.Context, session)
	if err != nil {
		return
	}
	if mode == "follow" {
		_, mode = runtime.Client.Status()
	}
	documents, err := api.database.ListDocuments(runtime.Context, session, jwxkArchiveNamespace)
	if err != nil {
		return
	}
	for _, document := range documents {
		var archive map[string]any
		if json.Unmarshal(document.Payload, &archive) != nil {
			continue
		}
		batch := academic.Text(archive["batch_code"])
		if !(academic.JWXKSearch{Batch: batch, Scope: "FANKC", Page: 1, Size: 50}).Valid() {
			continue
		}
		api.scheduleJWXKScan(runtime.Context, session, batch, mode)
	}
}

func (api *API) scanJWXKCatalog(ctx context.Context, session store.Session, runtime *Runtime, batch, mode string) (scanError error) {
	identifier := make([]byte, 16)
	if _, err := rand.Read(identifier); err != nil {
		return err
	}
	scanID := hex.EncodeToString(identifier)
	observed := map[string]bool{}
	defer func() {
		if scanError == nil {
			return
		}
		statusContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		defer cancel()
		api.resources.documentWorkStep(statusContext, session, func() error {
			document, archive, err := api.readJWXKArchive(statusContext, session, batch)
			if err != nil || archive == nil {
				return err
			}
			archive["sync_status"], archive["sync_error"] = "failed", "学校目录尚未完整读取，请稍后重试"
			archive["sync_retry_at"] = time.Now().UTC().Add(time.Minute).Format(time.RFC3339)
			if runtime.Context.Err() != nil {
				archive["sync_status"], archive["sync_error"] = "paused", "登录会话已结束"
			}
			return api.saveJWXKScan(statusContext, session, batch, document, archive)
		})
	}()
	var scopes []map[string]string
	err := api.resources.documentWorkStep(ctx, session, func() error {
		document, archive, err := api.readJWXKArchive(ctx, session, batch)
		if err != nil {
			return err
		}
		if archive == nil || document.Deleted {
			return store.ErrMissing
		}
		contextData, err := academic.FetchJWXKContext(ctx, runtime, mode)
		if err != nil {
			return err
		}
		var selected map[string]any
		for _, row := range jwxkArchiveRows(contextData["batches"]) {
			if academic.Text(row["code"]) == batch {
				selected = row
				break
			}
		}
		if selected == nil {
			return &campus.Error{Code: "JWXK_BATCH_MISSING", Message: "选课轮次已不可见"}
		}
		seen := map[string]bool{}
		menus, _ := selected["menus"].([]map[string]string)
		for _, menu := range menus {
			code := menu["code"]
			if code != "" && code != "ALL" && code != "ROUND" && code != "ALLKC" && !seen[code] {
				seen[code] = true
				scopes = append(scopes, menu)
			}
		}
		if len(scopes) == 0 {
			return &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "尚未取得本轮业务范围"}
		}
		archive["batch_name"] = selected["name"]
		options := []map[string]string{{"code": "ALL", "name": "所有课程"}, {"code": "ROUND", "name": "本轮课程"}}
		archive["scope_options"] = append(options, menus...)
		for _, field := range []string{"term_code", "term_name", "selection_type_code", "selection_type", "begin_time", "end_time", "menus", "week_range"} {
			archive[field] = selected[field]
		}
		archive["sync_status"], archive["sync_error"] = "running", ""
		archive["sync_loaded"], archive["sync_total"] = 0, 0
		archive["sync_current_scope"], archive["sync_current_page"] = "", 0
		return api.saveJWXKScan(ctx, session, batch, document, archive)
	})
	if err != nil {
		return err
	}
	loaded, total := 0, 0
	for _, scope := range scopes {
		scopeTotal := -1
		seenClasses := map[string]bool{}
		for page := 1; ; page++ {
			if page > 200 {
				return &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "目录超过扫描上限，未标记完成"}
			}
			err := api.resources.documentWorkStep(ctx, session, func() error {
				document, archive, err := api.readJWXKArchive(ctx, session, batch)
				if err != nil {
					return err
				}
				if archive == nil || document.Deleted {
					return store.ErrMissing
				}
				result, err := academic.SearchJWXKCourses(ctx, runtime, mode, academic.JWXKSearch{Batch: batch, Scope: scope["code"], Page: page, Size: 50})
				if err != nil {
					return err
				}
				currentTotal := int(academic.Number(result["total"]))
				if scopeTotal == -1 {
					scopeTotal = currentTotal
					total += scopeTotal
				}
				if currentTotal != scopeTotal || int(academic.Number(result["row_count"])) != min(50, max(0, scopeTotal-(page-1)*50)) {
					return &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "学校分页数量变化或缺失，未标记完成"}
				}
				courses := jwxkArchiveRows(result["courses"])
				newClasses := 0
				for _, course := range courses {
					class := academic.Text(course["class_id"])
					observed[class] = true
					course["market_scan_id"], course["capacity_updated_at"] = scanID, time.Now().UTC().Format(time.RFC3339)
					if class != "" && !seenClasses[class] {
						seenClasses[class] = true
						newClasses++
					}
					course["source_scopes"], course["source_tags"] = []string{scope["code"]}, []string{scope["name"]}
					course["teaching_class_type"] = scope["code"]
				}
				if scopeTotal > 0 && newClasses == 0 {
					return &campus.Error{Code: "JWXK_CATALOG_INCOMPLETE", Message: "学校目录分页没有新增教学班，未标记完成"}
				}
				archive["courses"] = academic.MergeJWXKArchive(jwxkArchiveRows(archive["courses"]), courses)
				loaded += int(academic.Number(result["row_count"]))
				archive["sync_loaded"], archive["sync_total"] = loaded, total
				archive["sync_current_scope"], archive["sync_current_page"] = scope["code"], page
				return api.saveJWXKScan(ctx, session, batch, document, archive)
			})
			if err != nil {
				return err
			}
			if page*50 >= scopeTotal {
				break
			}
		}
	}
	return api.resources.documentWorkStep(ctx, session, func() error {
		document, archive, err := api.readJWXKArchive(ctx, session, batch)
		if err != nil {
			return err
		}
		if archive == nil {
			return store.ErrMissing
		}
		archive["catalog_complete"], archive["sync_status"] = true, "complete"
		archive["completed_scan_id"], archive["completed_course_count"] = scanID, len(observed)
		archive["last_sync_at"], archive["sync_retry_at"] = time.Now().UTC().Format(time.RFC3339), ""
		return api.saveJWXKScan(ctx, session, batch, document, archive)
	})
}
