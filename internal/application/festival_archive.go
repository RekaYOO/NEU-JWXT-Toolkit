package application

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"fmt"
	"mime"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (runtime *Runtime) DownloadService(ctx context.Context, mode, path string, normalize func(string) string) (campus.ServiceFile, error) {
	client, ok := runtime.Client.(interface {
		DownloadService(context.Context, string, string, func(string) string) (campus.ServiceFile, error)
	})
	if !ok {
		return campus.ServiceFile{}, store.ErrAccess
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	if runtime.Context.Err() != nil {
		return campus.ServiceFile{}, store.ErrAccess
	}
	return client.DownloadService(ctx, mode, path, normalize)
}

func festivalImageType(file campus.ServiceFile) (string, error) {
	body := file.Body
	extension, expected := "", ""
	switch {
	case bytes.HasPrefix(body, []byte("\x89PNG\r\n\x1a\n")):
		extension, expected = "png", "image/png"
	case bytes.HasPrefix(body, []byte("\xff\xd8\xff")):
		extension, expected = "jpg", "image/jpeg"
	case bytes.HasPrefix(body, []byte("GIF87a")), bytes.HasPrefix(body, []byte("GIF89a")):
		extension, expected = "gif", "image/gif"
	case len(body) >= 12 && string(body[:4]) == "RIFF" && string(body[8:12]) == "WEBP":
		extension, expected = "webp", "image/webp"
	}
	contentType, _, err := mime.ParseMediaType(file.ContentType)
	if err != nil || expected == "" || contentType != expected || len(body) > 10*1024*1024 {
		return "", errors.New("证书图片格式或大小校验失败")
	}
	return extension, nil
}

func festivalArchiveLabel(start, end time.Time) string {
	if start.Month() == 8 && start.Day() == 31 && end.Year() == start.Year()+1 && end.Month() == 8 && end.Day() == 30 {
		return fmt.Sprintf("%d-%d学年", start.Year(), end.Year())
	}
	if start.Month() == 3 && start.Day() == 1 && end.Year() == start.Year() && end.Month() == 8 && end.Day() == 31 {
		return fmt.Sprintf("%d-%d春季学期", start.Year()-1, start.Year())
	}
	if start.Month() == 9 && start.Day() == 1 && end.Year() == start.Year()+1 && end.Month() == 2 && end.AddDate(0, 0, 1).Month() == 3 {
		return fmt.Sprintf("%d-%d秋季学期", start.Year(), end.Year())
	}
	return start.Format("2006-01-02") + "_" + end.Format("2006-01-02")
}

func (api *API) festivalArchive(response http.ResponseWriter, request *http.Request) {
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	var input struct {
		Start string `json:"start_date"`
		End   string `json:"end_date"`
	}
	if !decodeJSON(response, request, &input) {
		return
	}
	start, startErr := time.Parse("2006-01-02", input.Start)
	end, endErr := time.Parse("2006-01-02", input.End)
	if startErr != nil || endErr != nil || end.Before(start) || end.Sub(start) > 369*24*time.Hour {
		writeJSON(response, 422, map[string]string{"detail": "日期范围必须有效且不超过370天"})
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var buffer bytes.Buffer
	succeeded, failed := 0, 0
	noCandidates, tooMany := false, false
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		preference, _, err := api.festivalPreference(request.Context(), session)
		if err != nil {
			return err
		}
		mode := festivalMode(preference, runtime)
		snapshot, err := academic.FetchFestivalActivities(request.Context(), runtime, mode)
		if err != nil {
			return err
		}
		candidates := []academic.FestivalActivity{}
		for _, activity := range snapshot.Activities {
			if !activity.CertificateAvailable || activity.CertificateURL == "" || activity.StartTime == nil || len(*activity.StartTime) < 10 {
				continue
			}
			date, err := time.Parse("2006-01-02", (*activity.StartTime)[:10])
			if err == nil && !date.Before(start) && !date.After(end) {
				candidates = append(candidates, activity)
			}
		}
		if len(candidates) == 0 {
			noCandidates = true
			return nil
		}
		if len(candidates) > 200 {
			tooMany = true
			return nil
		}
		archive := zip.NewWriter(&buffer)
		defer archive.Close()
		failures := []string{}
		names := map[string]bool{}
		total := 0
		for _, activity := range candidates {
			if err := request.Context().Err(); err != nil {
				return err
			}
			section := map[string]string{"创意节": "originality", "科普节": "popscience", "科技节": "technical", "创业节": "business"}[activity.Section]
			normalize := func(value string) string { return academic.FestivalCertificateURL(value, section) }
			canonical := normalize(activity.CertificateURL)
			if canonical == "" {
				failures = append(failures, activity.Name+"：证书地址校验失败")
				continue
			}
			file, err := runtime.DownloadService(request.Context(), mode, canonical, normalize)
			if err != nil {
				failure := festivalFailure(err)
				if failure.Code == "CXCY_LOGIN_REQUIRED" || failure.Code == "WEBVPN_CAMPUS_NETWORK_BLOCKED" || errors.Is(err, store.ErrAccess) {
					return err
				}
				failures = append(failures, activity.Name+"：证书下载失败")
				continue
			}
			extension, err := festivalImageType(file)
			if err != nil || normalize(file.URL) == "" {
				failures = append(failures, activity.Name+"：图片或最终地址校验失败")
				continue
			}
			total += len(file.Body)
			if total > 100*1024*1024 {
				return &campus.Error{Code: "ARCHIVE_TOO_LARGE", Message: "证书包超过总大小限制"}
			}
			stem := strings.TrimSuffix(documentFilename((*activity.StartTime)[:10]+"_"+activity.Section+"_"+activity.Name, extension), "."+extension)
			name := stem + "." + extension
			for suffix := 2; names[name]; suffix++ {
				name = stem + "_" + strconv.Itoa(suffix) + "." + extension
			}
			names[name] = true
			entry, err := archive.Create(name)
			if err != nil {
				return err
			}
			if _, err := entry.Write(file.Body); err != nil {
				return err
			}
			succeeded++
		}
		failed = len(failures)
		if failed > 0 {
			entry, err := archive.Create("下载说明.txt")
			if err != nil {
				return err
			}
			if _, err := entry.Write([]byte("以下证书下载失败：\n" + strings.Join(failures, "\n"))); err != nil {
				return err
			}
		}
		return archive.Close()
	})
	if err != nil {
		var failure *campus.Error
		if errors.As(err, &failure) && failure.Code == "ARCHIVE_TOO_LARGE" {
			writeJSON(response, 413, map[string]string{"detail": failure.Message})
			return
		}
		api.festivalError(response, err)
		return
	}
	if noCandidates {
		writeJSON(response, 404, map[string]string{"detail": "所选范围内没有可下载的证书"})
		return
	}
	if tooMany {
		writeJSON(response, 413, map[string]string{"detail": "证书数量超过安全限制"})
		return
	}
	if succeeded == 0 {
		writeJSON(response, 502, map[string]string{"detail": "证书下载全部失败"})
		return
	}
	current, err := api.session(request)
	if err != nil || current.Account != session.Account || current.Generation != session.Generation {
		api.resourceError(response, store.ErrAccess)
		return
	}
	filename := "四节活动证书_" + festivalArchiveLabel(start, end) + ".zip"
	response.Header().Set("Content-Type", "application/zip")
	response.Header().Set("Content-Disposition", "attachment; filename=festival-certificates.zip; filename*=UTF-8''"+strings.ReplaceAll(url.QueryEscape(filename), "+", "%20"))
	response.Header().Set("Cache-Control", "no-store")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.Header().Set("X-Certificate-Succeeded", strconv.Itoa(succeeded))
	response.Header().Set("X-Certificate-Failed", strconv.Itoa(failed))
	response.WriteHeader(200)
	response.Write(buffer.Bytes())
}
