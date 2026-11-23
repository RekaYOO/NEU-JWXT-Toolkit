package application

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

type LegacyImportItem struct {
	Source    string `json:"source"`
	Namespace string `json:"namespace"`
	Key       string `json:"key"`
	Status    string `json:"status"`
}

type LegacyImportReport struct {
	Account  string             `json:"account"`
	Digest   string             `json:"digest"`
	Items    []LegacyImportItem `json:"items"`
	Warnings []string           `json:"warnings"`
	Applied  int                `json:"applied"`
}

type legacyCandidate struct {
	Source    string
	Namespace string
	Key       string
	Payload   json.RawMessage
}

func readLegacy(root *os.Root, name string, output any) (bool, error) {
	file, err := root.Open(name)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("cannot read legacy file %s", name)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4*1024*1024 {
		return false, fmt.Errorf("invalid or oversized legacy file %s", name)
	}
	decoder := json.NewDecoder(io.LimitReader(file, 4*1024*1024+1))
	decoder.UseNumber()
	if decoder.Decode(output) != nil || decoder.Decode(new(any)) != io.EOF {
		return false, fmt.Errorf("invalid legacy JSON in %s", name)
	}
	return true, nil
}

func legacyNames(root *os.Root, directory string) ([]string, error) {
	file, err := root.Open(directory)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()
	entries, err := file.ReadDir(-1)
	if err != nil {
		return nil, err
	}
	if len(entries) > 10000 {
		return nil, errors.New("legacy directory contains too many files")
	}
	names := []string{}
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".json") {
			names = append(names, filepath.Join(directory, entry.Name()))
		}
	}
	sort.Strings(names)
	return names, nil
}

func legacyCandidates(root *os.Root, account string, terms []string) ([]legacyCandidate, error) {
	result := []legacyCandidate{}
	add := func(source, namespace, key string, value any) error {
		payload, err := json.Marshal(value)
		if err != nil || len(payload) > store.DocumentPayloadLimit(namespace) {
			return errors.New("legacy document cannot be represented safely")
		}
		var canonical any
		decoder := json.NewDecoder(bytes.NewReader(payload))
		decoder.UseNumber()
		if err := decoder.Decode(&canonical); err != nil {
			return err
		}
		payload, err = json.Marshal(canonical)
		if err != nil {
			return err
		}
		result = append(result, legacyCandidate{source, namespace, key, payload})
		return nil
	}
	owner := sha256.Sum256([]byte(account))
	hash := hex.EncodeToString(owner[:])
	for _, directory := range []string{filepath.Join("成绩", "gpa_simulations", hash[:24]), "成绩"} {
		names, err := legacyNames(root, directory)
		if err != nil {
			return nil, err
		}
		for _, name := range names {
			var payload map[string]any
			if _, err := readLegacy(root, name, &payload); err != nil {
				return nil, err
			}
			metadata, _ := payload["export_info"].(map[string]any)
			if metadata["exported_by"] != account {
				if directory != "成绩" {
					return nil, fmt.Errorf("legacy GPA owner does not match in %s", name)
				}
				continue
			}
			filename, valid := gpaFilename(filepath.Base(name))
			courses, isList := payload["courses"].([]any)
			if !valid || !isList || len(courses) > 20000 {
				return nil, fmt.Errorf("invalid legacy GPA plan %s", name)
			}
			delete(payload, "_document_revision")
			if err := add(name, "gpa", filename, payload); err != nil {
				return nil, err
			}
		}
	}
	preferenceFile := filepath.Join("gpa_preferences", hash+".json")
	var preference struct {
		Mode *string `json:"mode"`
	}
	if found, err := readLegacy(root, preferenceFile, &preference); err != nil {
		return nil, err
	} else if found {
		if preference.Mode != nil && *preference.Mode != "through_2024" && *preference.Mode != "from_2025" {
			return nil, errors.New("invalid legacy GPA preference")
		}
		if err := add(preferenceFile, "gpa-policy", "default", preference); err != nil {
			return nil, err
		}
	}
	var favorites struct {
		Version int `json:"version"`
		Users   map[string]struct {
			Records []researchFavorite `json:"records"`
		} `json:"users"`
	}
	if found, err := readLegacy(root, "research_training_favorites.json", &favorites); err != nil {
		return nil, err
	} else if found {
		if favorites.Version != 1 {
			return nil, errors.New("unsupported legacy research favorites version")
		}
		for _, favorite := range favorites.Users[account].Records {
			if !academic.ValidEvaluationID(favorite.Batch) || !academic.ValidEvaluationID(favorite.Topic) || favorite.Snapshot == nil || academic.Text(favorite.Snapshot["topic_id"]) != favorite.Topic {
				return nil, errors.New("invalid legacy research favorite")
			}
			if batch := academic.Text(favorite.Snapshot["batch_id"]); batch != "" && batch != favorite.Batch {
				return nil, errors.New("legacy research favorite batch does not match")
			}
			for _, field := range []string{"contact", "application_reason", "rejection_reason", "phone", "email"} {
				delete(favorite.Snapshot, field)
			}
			if err := add("research_training_favorites.json", "research-favorites", researchFavoriteKey(favorite.Batch, favorite.Topic), favorite); err != nil {
				return nil, err
			}
		}
	}
	var config struct {
		Plans map[string]json.RawMessage `json:"course_selection_plans"`
	}
	if found, err := readLegacy(root, "config.json", &config); err != nil {
		return nil, err
	} else if found {
		for key, payload := range config.Plans {
			if !strings.HasPrefix(key, account+":") {
				continue
			}
			batch := strings.TrimPrefix(key, account+":")
			var plan academic.JWXKSavedPlan
			if json.Unmarshal(payload, &plan) != nil {
				return nil, errors.New("invalid legacy course selection plan")
			}
			if plan.Batch == "" {
				plan.Batch = batch
			}
			if plan.Batch != batch {
				return nil, errors.New("legacy course selection batch does not match owner key")
			}
			plan.Revision = 0
			if !plan.Valid() {
				return nil, errors.New("legacy course selection plan fails current validation")
			}
			if err := add("config.json", "selection-plans", batch, academic.NormalizeJWXKPlan(plan)); err != nil {
				return nil, err
			}
		}
	}
	for _, term := range terms {
		if !(academic.TimetableContext{Mode: "personal", Term: term}).Valid() {
			return nil, errors.New("legacy agenda requires valid explicit term codes")
		}
		termHash := sha256.Sum256([]byte(term))
		name := filepath.Join("timetable_agendas", hash, hex.EncodeToString(termHash[:])+".json")
		var record struct {
			AgendaDocument
			Semester *semesterSpan `json:"_semester"`
			Retired  bool          `json:"_retired"`
		}
		if found, err := readLegacy(root, name, &record); err != nil {
			return nil, err
		} else if found {
			if !record.AgendaDocument.normalize() || (record.Semester != nil && (!validDate(record.Semester.Start) || !validDate(record.Semester.End) || record.Semester.Start > record.Semester.End)) {
				return nil, errors.New("legacy agenda is invalid")
			}
			converted := agendaRecord{Events: record.Events, Moves: record.Moves, Semester: record.Semester, Retired: record.Retired}
			if record.Retired {
				converted.Events, converted.Moves = []AgendaEvent{}, []AgendaMove{}
			}
			if err := add(name, "agenda", term, converted); err != nil {
				return nil, err
			}
		}
	}
	archives, err := legacyArchives(root, account)
	if err != nil {
		return nil, err
	}
	for _, archive := range archives {
		if err := add("course_selection_catalog_history.json", jwxkArchiveNamespace, academic.Text(archive["archive_id"]), archive); err != nil {
			return nil, err
		}
	}
	var automationSettings map[string]json.RawMessage
	if found, err := readLegacy(root, "course_selection_automation_settings.json", &automationSettings); err != nil {
		return nil, err
	} else if found {
		defaults, _ := json.Marshal(academic.DefaultJWXKAutomationSettings())
		var allowed map[string]json.RawMessage
		json.Unmarshal(defaults, &allowed)
		for key, payload := range automationSettings {
			if !strings.HasPrefix(key, account+":") {
				continue
			}
			batch := strings.TrimPrefix(key, account+":")
			if !(academic.JWXKSearch{Batch: batch, Scope: "FANKC", Page: 1, Size: 20}).Valid() {
				return nil, errors.New("invalid legacy automation settings batch")
			}
			var fields map[string]json.RawMessage
			if json.Unmarshal(payload, &fields) != nil || fields == nil {
				return nil, errors.New("invalid legacy automation settings")
			}
			for field := range fields {
				if _, exists := allowed[field]; !exists {
					delete(fields, field)
				}
			}
			clean, _ := json.Marshal(fields)
			settings := academic.DefaultJWXKAutomationSettings()
			if json.Unmarshal(clean, &settings) != nil || !settings.Valid() {
				return nil, errors.New("legacy automation settings fail current validation")
			}
			if err := add("course_selection_automation_settings.json", jwxkAutomationSettingsNamespace, batch, settings); err != nil {
				return nil, err
			}
		}
	}
	tasks, err := legacyTasks(root, account)
	if err != nil {
		return nil, err
	}
	for _, task := range tasks {
		var value any
		decoder := json.NewDecoder(bytes.NewReader(task.Payload))
		decoder.UseNumber()
		if err := decoder.Decode(&value); err != nil {
			return nil, err
		}
		if err := add(task.Source, task.Namespace, task.Key, value); err != nil {
			return nil, err
		}
	}
	sort.Slice(result, func(left, right int) bool {
		return result[left].Namespace+"\x00"+result[left].Key+"\x00"+result[left].Source < result[right].Namespace+"\x00"+result[right].Key+"\x00"+result[right].Source
	})
	unique := []legacyCandidate{}
	totalBytes := 0
	for _, candidate := range result {
		if len(unique) > 0 {
			previous := unique[len(unique)-1]
			if candidate.Namespace == previous.Namespace && candidate.Key == previous.Key {
				if !bytes.Equal(candidate.Payload, previous.Payload) {
					return nil, errors.New("legacy sources contain different versions of the same document")
				}
				continue
			}
		}
		unique = append(unique, candidate)
		totalBytes += len(candidate.Payload)
	}
	if len(unique) > 256 || totalBytes > store.MaxDocumentBatchBytes {
		return nil, errors.New("legacy import exceeds the 256 document or 128 MiB transaction limit")
	}
	return unique, nil
}

func ImportLegacy(ctx context.Context, database *store.Store, source, account, expectedDigest string, terms ...string) (LegacyImportReport, error) {
	report := LegacyImportReport{Account: account, Items: []LegacyImportItem{}, Warnings: []string{}}
	normalized, err := campus.NormalizeAccount(account)
	if err != nil || normalized != account {
		return report, errors.New("explicit normalized account is required")
	}
	generation, err := database.Generation(ctx, account)
	if err != nil {
		return report, err
	}
	root, err := os.OpenRoot(source)
	if err != nil {
		return report, err
	}
	defer root.Close()
	candidates, err := legacyCandidates(root, account, terms)
	if err != nil {
		return report, err
	}
	digestPayload, _ := json.Marshal(struct {
		Account    string
		Generation int64
		Documents  []legacyCandidate
	}{account, generation, candidates})
	digest := sha256.Sum256(digestPayload)
	report.Digest = hex.EncodeToString(digest[:])
	changes := []store.DocumentChange{}
	conflict := false
	for _, candidate := range candidates {
		item := LegacyImportItem{candidate.Source, candidate.Namespace, candidate.Key, "new"}
		document, err := database.Read(ctx, account, candidate.Namespace, candidate.Key)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return report, err
		}
		if err == nil {
			var current any
			decoder := json.NewDecoder(bytes.NewReader(document.Payload))
			decoder.UseNumber()
			decodeErr := decoder.Decode(&current)
			canonical, _ := json.Marshal(current)
			if !document.Deleted && decodeErr == nil && bytes.Equal(canonical, candidate.Payload) {
				item.Status = "unchanged"
			} else {
				item.Status = "conflict"
				conflict = true
			}
		} else {
			changes = append(changes, store.DocumentChange{Namespace: candidate.Namespace, Key: candidate.Key, Payload: candidate.Payload})
		}
		report.Items = append(report.Items, item)
	}
	for _, name := range []string{"config.json", "timetable_agendas", "grade_tracking_config.json", "system_mail_config.json", "auth_recovery_config.json", "course_selection_settings.json", "credentials.json", "session.pkl"} {
		if file, err := root.Open(name); err == nil {
			file.Close()
			if name == "config.json" {
				report.Warnings = append(report.Warnings, "config.json: only explicitly account-owned course selection plans are included")
			} else if name == "timetable_agendas" {
				report.Warnings = append(report.Warnings, "timetable_agendas: only explicitly supplied term codes are included; other hashed term files are not inferred")
			} else {
				report.Warnings = append(report.Warnings, name+": not imported by this command")
			}
		}
	}
	if expectedDigest == "" {
		return report, nil
	}
	if expectedDigest != report.Digest {
		return report, errors.New("legacy preview digest changed; preview again before importing")
	}
	if conflict {
		return report, errors.New("target has conflicts or deletion tombstones; no documents were imported")
	}
	if len(changes) == 0 {
		return report, nil
	}
	if _, err := database.SaveBatchForSession(ctx, store.Session{Account: account, Generation: generation}, changes); err != nil {
		return report, err
	}
	report.Applied = len(changes)
	return report, nil
}
