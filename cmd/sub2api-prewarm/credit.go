package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/service"
)

const maxCreditOperations = 10000
const maxCreditInspectionsPerCycle = 50

// This is an internal supply-credit audit, never a customer financial ledger.
// The single installation lock held by watch protects all intent transitions.
type creditOperation struct {
	ID             string    `json:"operation_id"`
	ConfigSHA256   string    `json:"config_sha256"`
	Task           task      `json:"task"`
	UpstreamUserID int       `json:"upstream_user_id"`
	BaseURL        string    `json:"base_url"`
	Amount         float64   `json:"amount"`
	BeforeBalance  float64   `json:"before_balance"`
	AfterBalance   float64   `json:"after_balance"`
	CreatedAt      time.Time `json:"created_at"`
	ExpiresAt      time.Time `json:"expires_at"`
	NotBefore      time.Time `json:"not_before"`
	Status         string    `json:"status"`
	Reason         string    `json:"reason,omitempty"`
	Attempts       int       `json:"attempts"`
}

type creditLedger struct {
	Version          int               `json:"version"`
	NextCheckAt      time.Time         `json:"next_check_at"`
	ScanCursor       int               `json:"scan_cursor"`
	InspectionStatus string            `json:"inspection_status,omitempty"`
	InspectionReason string            `json:"inspection_reason,omitempty"`
	Operations       []creditOperation `json:"operations"`
}

func finiteCredit(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }

func creditIdentity(t task) service.Sub2APISubject {
	return service.Sub2APISubject{UserID: t.UserID, WorkspaceTeamID: t.TeamID}
}

func creditOperationID() (string, error) {
	var raw [24]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return "realyu-credit-" + hex.EncodeToString(raw[:]), nil
}

// creditCycle only consumes already-DONE jobs from this config generation. A
// single durable ledger also prevents config edits from hiding unresolved adds.
func (w worker) creditCycle(ctx context.Context, cfg *service.Sub2APIConfig, q queue, stateDir string, resume bool) (string, error) {
	if !cfg.Credit.Enabled {
		return "disabled", nil
	}
	if w.InspectCredit == nil || w.AddCredit == nil {
		return "error", errors.New("credit helpers unavailable")
	}
	path := filepath.Join(stateDir, "credit.json")
	configPath, err := canonicalPath(os.Getenv("REALYU_SUB2API_BINDINGS_FILE"))
	if err != nil {
		return "error", err
	}
	for _, input := range []string{configPath, configPath + ".prewarm.lock", configPath + ".prewarm-rate.json", filepath.Join(stateDir, "heartbeat.json"), filepath.Join(stateDir, cfg.ConfigSHA256()+".json"), filepath.Join(stateDir, cfg.ConfigSHA256()+".queue.json")} {
		if sameFile(path, input) {
			return "blocked", errors.New("credit audit overlaps private state")
		}
	}
	ledger := creditLedger{Version: 1, Operations: []creditOperation{}}
	if _, err := readPrivateJSON(path, &ledger); err != nil && !os.IsNotExist(err) {
		return "blocked", errors.New("credit audit unavailable")
	}
	if ledger.Version != 1 || ledger.Operations == nil || len(ledger.Operations) > maxCreditOperations || ledger.ScanCursor < 0 {
		return "blocked", errors.New("invalid credit audit")
	}
	if ledger.InspectionStatus == "blocked" {
		if !resume {
			return "blocked", errors.New("credit inspection requires operator action")
		}
		ledger.InspectionStatus, ledger.InspectionReason = "", ""
		ledger.NextCheckAt = time.Time{}
		if err := writeAtomicJSON(path, ledger); err != nil {
			return "error", err
		}
	}
	var progress state
	_, err = readPrivateJSON(filepath.Join(stateDir, cfg.ConfigSHA256()+".json"), &progress)
	if err != nil && !os.IsNotExist(err) {
		return "error", errors.New("projection progress unavailable")
	}
	if err == nil && (progress.Version != 1 || progress.ConfigSHA256 != cfg.ConfigSHA256()) {
		return "blocked", errors.New("projection generation mismatch")
	}
	eligible := make(map[string]bool)
	for _, t := range q.Tasks {
		if progress.Tasks[t.key()].Status == "DONE" {
			eligible[t.key()] = true
		}
	}
	seenOperations := make(map[string]bool)
	for i := range ledger.Operations {
		op := &ledger.Operations[i]
		if !strings.HasPrefix(op.ID, "realyu-credit-") || len(op.ID) != 62 || seenOperations[op.ID] || !finiteCredit(op.Amount) || op.Amount <= 0 || op.UpstreamUserID <= 0 || op.CreatedAt.IsZero() || !op.ExpiresAt.After(op.CreatedAt) {
			return "blocked", errors.New("invalid credit intent")
		}
		seenOperations[op.ID] = true
		if op.Status == "CONFIRMED" {
			continue
		}
		if op.ConfigSHA256 != cfg.ConfigSHA256() || op.Amount != cfg.Credit.TopupAmount || !eligible[op.Task.key()] {
			return "blocked", errors.New("unresolved credit intent requires original authorized generation")
		}
		if !w.Now().Before(op.ExpiresAt) || w.Now().Before(op.CreatedAt) {
			op.Status, op.Reason = "BLOCKED", "idempotency_window_expired"
			if err := writeAtomicJSON(path, ledger); err != nil {
				return "error", err
			}
			return "blocked", errors.New("credit intent requires manual audit")
		}
		// Unknown results and crash-interrupted writes never auto-retry. Explicit
		// resume is one operator-reviewed replay, never a new operation/key.
		if op.Status != "RETRY_WAIT" && !resume {
			return "blocked", errors.New("credit intent requires manual reconciliation")
		}
		if w.Now().Before(op.NotBefore) {
			return "checking", nil
		}
		return w.applyCredit(ctx, cfg, path, &ledger, i)
	}
	if w.Now().Before(ledger.NextCheckAt) {
		if ledger.InspectionStatus != "" {
			return ledger.InspectionStatus, nil
		}
		return "ready", nil
	}
	seenUsers := make(map[string]bool)
	inspections := 0
	for idx := ledger.ScanCursor; idx < len(q.Tasks); idx++ {
		t := q.Tasks[idx]
		if !eligible[t.key()] {
			continue
		}
		if inspections >= maxCreditInspectionsPerCycle {
			ledger.ScanCursor = idx
			ledger.NextCheckAt = w.Now().Add(5 * time.Second).UTC()
			if err := writeAtomicJSON(path, ledger); err != nil {
				return "error", err
			}
			return "checking", nil
		}
		if err := ctx.Err(); err != nil {
			return "error", err
		}
		account, err := w.InspectCredit(ctx, creditIdentity(t), t.ChannelID, cfg.ConfigSHA256())
		if err != nil {
			ledger.InspectionStatus, ledger.InspectionReason = "error", "inspection_unavailable"
			ledger.NextCheckAt = w.Now().Add(time.Minute).UTC()
			var management *service.Sub2APIManagementError
			if errors.As(err, &management) {
				if management.Status == 429 {
					ledger.InspectionStatus, ledger.InspectionReason = "checking", "rate_limited"
					ledger.NextCheckAt = w.Now().Add(service.Sub2APIPrewarmRetryDelay(err)).UTC()
				} else if management.Status >= 400 && management.Status < 500 {
					ledger.InspectionStatus, ledger.InspectionReason = "blocked", "inspection_rejected"
				}
			}
			if saveErr := writeAtomicJSON(path, ledger); saveErr != nil {
				return "error", saveErr
			}
			return ledger.InspectionStatus, errors.New("internal credit inspection unavailable")
		}
		if account.UpstreamUserID <= 0 || !finiteCredit(account.Balance) || account.BaseURL == "" {
			return "blocked", errors.New("invalid internal credit account")
		}
		inspections++
		key := fmt.Sprintf("%s:%d", account.BaseURL, account.UpstreamUserID)
		if seenUsers[key] {
			continue
		}
		seenUsers[key] = true
		if account.Balance >= cfg.Credit.LowWatermark {
			continue
		}
		if len(ledger.Operations) >= maxCreditOperations {
			return "blocked", errors.New("credit audit archive required")
		}
		id, err := creditOperationID()
		if err != nil {
			return "error", err
		}
		op := creditOperation{ID: id, ConfigSHA256: cfg.ConfigSHA256(), Task: t, UpstreamUserID: account.UpstreamUserID, BaseURL: account.BaseURL, Amount: cfg.Credit.TopupAmount, BeforeBalance: account.Balance, CreatedAt: w.Now().UTC(), ExpiresAt: w.Now().Add(time.Duration(cfg.Credit.IdempotencyWindowSeconds) * time.Second).UTC(), Status: "INTENT"}
		ledger.Operations = append(ledger.Operations, op)
		// Persist immutable operation identity and payload before a network write.
		if err := writeAtomicJSON(path, ledger); err != nil {
			return "error", err
		}
		status, err := w.applyCredit(ctx, cfg, path, &ledger, len(ledger.Operations)-1)
		if err != nil || status != "ready" {
			return status, err
		}
	}
	ledger.ScanCursor = 0
	ledger.InspectionStatus, ledger.InspectionReason = "", ""
	ledger.NextCheckAt = w.Now().Add(time.Duration(cfg.Credit.CheckIntervalSeconds) * time.Second).UTC()
	if err := writeAtomicJSON(path, ledger); err != nil {
		return "error", err
	}
	return "ready", nil
}

func (w worker) applyCredit(ctx context.Context, cfg *service.Sub2APIConfig, path string, ledger *creditLedger, index int) (string, error) {
	op := &ledger.Operations[index]
	op.Status, op.Reason = "IN_FLIGHT", ""
	op.Attempts++
	if err := writeAtomicJSON(path, ledger); err != nil {
		return "error", err
	}
	account, err := w.AddCredit(ctx, creditIdentity(op.Task), op.Task.ChannelID, op.ConfigSHA256, op.UpstreamUserID, op.ID)
	if err != nil {
		op.Status, op.Reason = "BLOCKED", "upstream_rejected"
		var management *service.Sub2APIManagementError
		if service.Sub2APICreditOutcomeUncertain(err) || ctx.Err() != nil || !errors.As(err, &management) || management.Status >= 500 {
			op.Reason = "outcome_unknown"
		}
		if errors.As(err, &management) && management.Status == 429 && !service.Sub2APICreditOutcomeUncertain(err) {
			op.Status, op.Reason = "RETRY_WAIT", "rate_limited"
			op.NotBefore = w.Now().Add(service.Sub2APIPrewarmRetryDelay(err)).UTC()
		}
		if saveErr := writeAtomicJSON(path, ledger); saveErr != nil {
			return "error", saveErr
		}
		if op.Status == "RETRY_WAIT" {
			return "checking", nil
		}
		return "blocked", errors.New("internal credit requires operator action")
	}
	if account.UpstreamUserID != op.UpstreamUserID || account.BaseURL != op.BaseURL || !finiteCredit(account.Balance) {
		op.Status, op.Reason = "BLOCKED", "outcome_unknown"
		if err := writeAtomicJSON(path, ledger); err != nil {
			return "error", err
		}
		return "blocked", errors.New("invalid internal credit receipt")
	}
	op.Status, op.Reason, op.AfterBalance = "CONFIRMED", "", account.Balance
	if err := writeAtomicJSON(path, ledger); err != nil {
		return "error", err
	}
	return "ready", nil
}
