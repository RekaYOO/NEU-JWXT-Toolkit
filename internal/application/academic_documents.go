package application

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"unicode"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
	"golang.org/x/net/html/charset"
)

var documentHead = regexp.MustCompile(`(?i)<head(?:\s[^>]*)?>`)

func (runtime *Runtime) GenerateAcademicDocument(ctx context.Context, identifier string) (campus.AcademicDocumentFile, error) {
	client, ok := runtime.Client.(interface {
		GenerateAcademicDocument(context.Context, string) (campus.AcademicDocumentFile, error)
	})
	if !ok {
		return campus.AcademicDocumentFile{}, store.ErrAccess
	}
	requestContext, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(runtime.Context, cancel)
	defer stop()
	if runtime.Context.Err() != nil {
		return campus.AcademicDocumentFile{}, store.ErrAccess
	}
	return client.GenerateAcademicDocument(requestContext, identifier)
}

func (api *API) registerAcademicDocuments(router *http.ServeMux) {
	router.HandleFunc("GET /api/export/academic-documents", api.academicDocuments)
	router.HandleFunc("POST /api/export/academic-documents/generate", api.academicDocuments)
}

func documentFilename(name, extension string) string {
	clean := strings.Map(func(character rune) rune {
		if unicode.IsControl(character) || strings.ContainsRune(`\/:*?"<>|`, character) {
			return '_'
		}
		return character
	}, name)
	characters := []rune(clean)
	if len(characters) > 100 {
		clean = string(characters[:100])
	}
	clean = strings.Trim(clean, " ._")
	if clean == "" {
		clean = "教务证明"
	}
	return clean + "." + extension
}

func prepareAcademicDocument(file campus.AcademicDocumentFile) ([]byte, string, error) {
	invalid := &campus.Error{Code: "DOCUMENT_RESULT_UNKNOWN", Message: "学校未返回有效证明，请核对打印记录；不会自动重试"}
	if len(file.Body) == 0 || len(file.Body) > campus.MaxAcademicDocumentBytes {
		return nil, "", invalid
	}
	if bytes.HasPrefix(file.Body, []byte("%PDF-")) {
		return file.Body, "pdf", nil
	}
	if !strings.Contains(strings.ToLower(file.ContentType), "text/html") {
		return nil, "", invalid
	}
	reader, err := charset.NewReader(bytes.NewReader(file.Body), file.ContentType)
	if err != nil {
		return nil, "", invalid
	}
	body, err := io.ReadAll(io.LimitReader(reader, campus.MaxAcademicDocumentBytes+1))
	if err != nil || len(body) > campus.MaxAcademicDocumentBytes {
		return nil, "", invalid
	}
	lower := strings.ToLower(string(body))
	if !strings.Contains(lower, "<html") && !strings.Contains(lower, "<head") {
		return nil, "", invalid
	}
	if strings.Contains(lower, "name=\"password\"") || strings.Contains(lower, "id=\"loginform\"") || strings.Contains(lower, "/tpass/login") {
		return nil, "", invalid
	}
	base := []byte(`<meta charset="utf-8"><base href="https://jwxt.neu.edu.cn/jwapp/sys/zmdyneu/">`)
	if location := documentHead.FindIndex(body); location != nil {
		result := append([]byte{}, body[:location[1]]...)
		result = append(result, base...)
		body = append(result, body[location[1]:]...)
	} else {
		body = append(base, body...)
	}
	return body, "html", nil
}

func (api *API) academicDocuments(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
	response.Header().Set("Pragma", "no-cache")
	session, ok := api.requireSession(response, request)
	if !ok {
		return
	}
	runtime := api.outlineRuntime(response, request)
	if runtime == nil {
		return
	}
	var input struct {
		ID string `json:"document_id"`
	}
	generate := request.Method == http.MethodPost
	if generate {
		if !decodeJSON(response, request, &input) {
			return
		}
		if !academic.ValidEvaluationID(input.ID) {
			writeJSON(response, 422, map[string]string{"detail": "证明编号无效"})
			return
		}
	}
	var documents []academic.AcademicDocument
	var document academic.AcademicDocument
	var body []byte
	var format string
	err := api.resources.documentWorkStep(request.Context(), session, func() error {
		var err error
		if !generate {
			documents, err = academic.FetchAcademicDocuments(request.Context(), runtime)
			return err
		}
		document, err = academic.PreflightAcademicDocument(request.Context(), runtime, input.ID)
		if err != nil {
			return err
		}
		file, err := runtime.GenerateAcademicDocument(request.Context(), input.ID)
		if err != nil {
			return err
		}
		body, format, err = prepareAcademicDocument(file)
		return err
	})
	if err != nil {
		var failure *campus.Error
		if errors.As(err, &failure) && failure.Code == "ACADEMIC_DOCUMENT_REJECTED" {
			writeJSON(response, 409, map[string]string{"detail": failure.Message, "error_code": failure.Code})
			return
		}
		api.resourceError(response, err)
		return
	}
	current, err := api.session(request)
	if err != nil || current.Account != session.Account || current.Generation != session.Generation {
		api.resourceError(response, store.ErrAccess)
		return
	}
	if !generate {
		groups := []map[string]any{}
		indexes := map[string]int{}
		for _, item := range documents {
			index, exists := indexes[item.Category]
			if !exists {
				index = len(groups)
				indexes[item.Category] = index
				groups = append(groups, map[string]any{"name": item.Category, "documents": []academic.AcademicDocument{}})
			}
			groups[index]["documents"] = append(groups[index]["documents"].([]academic.AcademicDocument), item)
		}
		writeJSON(response, 200, map[string]any{"documents": documents, "groups": groups})
		return
	}
	response.Header().Set("Content-Type", "application/pdf")
	if format == "html" {
		response.Header().Set("Content-Type", "text/html; charset=utf-8")
	}
	response.Header().Set("Content-Disposition", "inline; filename*=UTF-8''"+strings.ReplaceAll(url.QueryEscape(documentFilename(document.Name, format)), "+", "%20"))
	response.Header().Set("X-Academic-Document-Name", strings.ReplaceAll(url.QueryEscape(document.Name), "+", "%20"))
	response.Header().Set("X-Academic-Document-Format", format)
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.WriteHeader(200)
	response.Write(body)
}
