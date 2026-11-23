package application

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/academic"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/campus"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func legacySharedCandidates(root *os.Root, remote bool) ([]legacyCandidate, []byte, error) {
	candidates := []legacyCandidate{}
	add := func(source, namespace, key string, value any) {
		payload, _ := json.Marshal(value)
		candidates = append(candidates, legacyCandidate{source, namespace, key, payload})
	}
	var tracking map[string]json.RawMessage
	found, err := readLegacy(root, "grade_tracking_config.json", &tracking)
	if err != nil {
		return nil, nil, err
	}
	if found {
		if tracking == nil {
			return nil, nil, errors.New("invalid legacy tracking config")
		}
		config := academic.DefaultGradeTrackingConfig()
		payload, _ := json.Marshal(tracking)
		if json.Unmarshal(payload, &config) != nil || !config.Valid() {
			return nil, nil, errors.New("invalid legacy tracking schedule")
		}
		config.Enabled = false
		add("grade_tracking_config.json", "grade-tracking", "config", config)
	}
	var mailFields map[string]json.RawMessage
	mailFound, err := readLegacy(root, "system_mail_config.json", &mailFields)
	if err != nil {
		return nil, nil, err
	}
	mailSource := "system_mail_config.json"
	if mailFound && mailFields == nil {
		return nil, nil, errors.New("invalid legacy mail config")
	}
	if !mailFound {
		mailFields = map[string]json.RawMessage{}
		for _, key := range strings.Fields("smtp_host smtp_port smtp_security smtp_username smtp_password from_email to_email") {
			if value, exists := tracking[key]; exists {
				mailFields[key] = value
			}
		}
		mailFound = len(mailFields) > 0
		mailSource = "grade_tracking_config.json"
	}
	var mailPayload []byte
	if mailFound {
		config := notifications.DefaultMailConfig()
		payload, _ := json.Marshal(mailFields)
		if json.Unmarshal(payload, &config) != nil || !config.Valid(false) {
			return nil, nil, errors.New("invalid legacy mail settings")
		}
		mailPayload, _ = json.Marshal(config)
		add(mailSource, "settings", "legacy-mail-import", map[string]any{"version": 1})
	}
	var recoveryFields map[string]json.RawMessage
	recoveryFound, err := readLegacy(root, "auth_recovery_config.json", &recoveryFields)
	if err != nil {
		return nil, nil, err
	}
	recoverySource := "auth_recovery_config.json"
	if recoveryFound && recoveryFields == nil {
		return nil, nil, errors.New("invalid legacy recovery config")
	}
	if !recoveryFound {
		if value, exists := tracking["site_url"]; exists {
			recoveryFields = map[string]json.RawMessage{"public_base_url": value}
			recoveryFound = true
			recoverySource = "grade_tracking_config.json"
		}
	}
	if recoveryFound {
		config := recoverySettings{Hours: 3}
		payload, _ := json.Marshal(recoveryFields)
		if json.Unmarshal(payload, &config) != nil {
			return nil, nil, errors.New("invalid legacy recovery settings")
		}
		config.BaseURL = strings.TrimRight(strings.TrimSpace(config.BaseURL), "/")
		if !validRecoveryBase(config.BaseURL, remote) || config.Hours < 1 || config.Hours > 168 {
			return nil, nil, errors.New("invalid legacy recovery address or duration")
		}
		config.Epoch = "legacy-import-v1"
		add(recoverySource, "auth-recovery", "config", config)
	}
	return candidates, mailPayload, nil
}

func ImportLegacySharedSettings(ctx context.Context, database *store.Store, source, account, owner, expectedDigest string) (LegacyImportReport, error) {
	report := LegacyImportReport{Account: account, Items: []LegacyImportItem{}, Warnings: []string{"Shared settings are assigned only to the explicitly named owner. Tracking stays disabled; old mail queues, sessions and recovery links are not imported."}}
	normalized, err := campus.NormalizeAccount(account)
	if err != nil || normalized != account || owner != account {
		return report, errors.New("shared settings owner must explicitly match the destination account")
	}
	generation, err := database.Generation(ctx, account)
	if err != nil {
		return report, err
	}
	mode, err := database.SiteSetting(ctx, "deployment-mode")
	if err != nil && !errors.Is(err, store.ErrMissing) {
		return report, err
	}
	root, err := os.OpenRoot(source)
	if err != nil {
		return report, err
	}
	defer root.Close()
	candidates, mailPayload, err := legacySharedCandidates(root, string(mode) != "local-single")
	if err != nil {
		return report, err
	}
	payload, _ := json.Marshal(struct {
		Account    string
		Generation int64
		Mode       string
		Documents  []legacyCandidate
		Mail       json.RawMessage
	}{account, generation, string(mode), candidates, mailPayload})
	digest := sha256.Sum256(payload)
	report.Digest = hex.EncodeToString(digest[:])
	changes := []store.DocumentChange{}
	conflict := false
	markerExists := false
	for _, candidate := range candidates {
		item := LegacyImportItem{candidate.Source, candidate.Namespace, candidate.Key, "new"}
		document, err := database.Read(ctx, account, candidate.Namespace, candidate.Key)
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return report, err
		}
		if err == nil {
			if candidate.Key == "legacy-mail-import" {
				markerExists = true
			}
			var current any
			decodeErr := json.Unmarshal(document.Payload, &current)
			canonical, _ := json.Marshal(current)
			var incoming any
			json.Unmarshal(candidate.Payload, &incoming)
			incomingCanonical, _ := json.Marshal(incoming)
			if !document.Deleted && decodeErr == nil && bytes.Equal(canonical, incomingCanonical) {
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
	var newMail []byte
	if mailPayload != nil {
		item := LegacyImportItem{"system_mail_config.json / grade_tracking_config.json", "encrypted-settings", "mail-config", "new"}
		current, err := database.ReadSecret(ctx, account, "mail-config")
		if err != nil && !errors.Is(err, store.ErrMissing) {
			return report, err
		}
		if err == nil {
			config := notifications.DefaultMailConfig()
			decodeErr := json.Unmarshal(current, &config)
			canonical, _ := json.Marshal(config)
			if decodeErr == nil && bytes.Equal(canonical, mailPayload) {
				item.Status = "unchanged"
			} else {
				item.Status = "conflict"
				conflict = true
			}
		} else if markerExists {
			item.Status = "conflict"
			conflict = true
		} else {
			newMail = mailPayload
		}
		report.Items = append(report.Items, item)
	}
	if expectedDigest == "" {
		return report, nil
	}
	if expectedDigest != report.Digest {
		return report, errors.New("shared settings preview changed; preview again")
	}
	if conflict {
		return report, errors.New("shared settings conflict with destination; nothing imported")
	}
	if len(changes) == 0 {
		return report, nil
	}
	if _, err := database.ImportSettingsForSession(ctx, store.Session{Account: account, Generation: generation}, changes, newMail); err != nil {
		return report, err
	}
	report.Applied = len(changes)
	if newMail != nil {
		report.Applied++
	}
	return report, nil
}
