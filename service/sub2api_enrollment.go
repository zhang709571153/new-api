package service

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/ratio_setting"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

var errSub2APIEnrollmentIneligible = errors.New("Sub2API enrollment subject is unavailable")

type Sub2APIEnrollmentStatus struct {
	Enabled            bool       `json:"enabled"`
	Status             string     `json:"status"`
	Reason             string     `json:"reason"`
	PendingCount       int        `json:"pending_count"`
	ReadyCount         int        `json:"ready_count"`
	ErrorCount         int        `json:"error_count"`
	WorkerLastSeen     *time.Time `json:"worker_last_seen,omitempty"`
	ScannerLastSuccess *time.Time `json:"scanner_last_success,omitempty"`
	ScannerStatus      string     `json:"scanner_status"`
}

var sub2APIEnrollmentScan struct {
	sync.RWMutex
	LastSuccess time.Time
	Status      string
}

// QueueSub2APIIdentity accepts a previously authenticated/authorized subject.
// Producers only create immutable job files; only the worker can write progress.
func QueueSub2APIIdentity(cfg *Sub2APIConfig, subject Sub2APISubject, channelID int) error {
	root := strings.TrimSpace(os.Getenv("REALYU_SUB2API_QUEUE_DIR"))
	if root == "" || subject.UserID <= 0 || subject.WorkspaceTeamID < 0 {
		return errors.New("Sub2API enrollment queue is unavailable")
	}
	poolKnown := false
	for _, pool := range cfg.Pools {
		if pool.ChannelID == channelID {
			poolKnown = true
			break
		}
	}
	if !poolKnown {
		return errors.New("Sub2API enrollment pool is unavailable")
	}
	dir := filepath.Join(root, cfg.digest)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return errors.New("Sub2API enrollment queue is unavailable")
	}
	path := filepath.Join(dir, fmt.Sprintf("%d-%d-%d.json", subject.UserID, subject.WorkspaceTeamID, channelID))
	task := map[string]any{"realyu_user_id": subject.UserID, "workspace_team_id": subject.WorkspaceTeamID, "channel_id": channelID}
	raw, err := common.Marshal(map[string]any{"version": 1, "config_sha256": cfg.digest, "task": task})
	if err != nil {
		return err
	}
	if existing, err := os.ReadFile(path); err == nil {
		if string(existing) != string(raw) {
			return errors.New("Sub2API enrollment job is inconsistent")
		}
		return nil
	} else if !os.IsNotExist(err) {
		return errors.New("Sub2API enrollment queue is unavailable")
	}
	f, err := os.CreateTemp(dir, ".enqueue-*.tmp")
	if err != nil {
		return errors.New("Sub2API enrollment queue is unavailable")
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(raw); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return errors.New("Sub2API enrollment queue write failed")
	}
	// Hard-link publication is atomic and cannot replace another producer's
	// complete job. The temporary file and final job are on the same volume.
	if err := os.Link(f.Name(), path); err != nil {
		if !os.IsExist(err) {
			return errors.New("Sub2API enrollment queue publish failed")
		}
		existing, readErr := os.ReadFile(path)
		if readErr != nil || string(existing) != string(raw) {
			return errors.New("Sub2API enrollment job is inconsistent")
		}
	}
	return nil
}

func sub2APIWorkspacePools(cfg *Sub2APIConfig, userID int, scope string) (Sub2APISubject, []Sub2APIPool, error) {
	user, err := model.GetUserById(userID, false)
	if err != nil {
		return Sub2APISubject{}, nil, err
	}
	if user.Status != common.UserStatusEnabled {
		return Sub2APISubject{}, nil, errSub2APIEnrollmentIneligible
	}
	if scope == "" {
		scope = "current"
	}
	if scope != "personal" && scope != "team" && scope != "current" {
		return Sub2APISubject{}, nil, model.ErrWorkspaceAccess
	}
	// The normal key helper locks the customer's row, even for create=false.
	// Reconciliation instead uses plain reads and never joins a business
	// transaction or blocks it with SELECT FOR UPDATE.
	member, team, err := model.FindWorkspaceMembership(userID)
	if err != nil {
		return Sub2APISubject{}, nil, err
	}
	var token *model.Token
	if scope != "personal" && member != nil && team != nil {
		token = &model.Token{}
		if err := model.DB.Where("id = ? AND user_id = ? AND workspace_user_id = ?", member.TokenID, team.OwnerUserID, userID).First(token).Error; err != nil {
			return Sub2APISubject{}, nil, err
		}
	} else {
		if scope == "team" {
			return Sub2APISubject{}, nil, model.ErrWorkspaceAccess
		}
		var mapping model.WorkspacePersonalKey
		mappingErr := model.DB.Where("user_id = ?", userID).First(&mapping).Error
		if mappingErr != nil && !errors.Is(mappingErr, gorm.ErrRecordNotFound) {
			return Sub2APISubject{}, nil, mappingErr
		}
		if mappingErr == nil {
			token = &model.Token{}
			err = model.DB.Where("id = ? AND user_id = ? AND workspace_user_id = 0", mapping.TokenID, userID).First(token).Error
			if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
				return Sub2APISubject{}, nil, err
			}
			if err != nil {
				token = nil
			}
		}
		if token == nil {
			candidate := &model.Token{}
			err = model.DB.Where("user_id = ? AND workspace_user_id = 0 AND status = ? AND unlimited_quota = ? AND model_limits_enabled = ? AND expired_time = -1 AND (allow_ips IS NULL OR allow_ips = '')", userID, common.TokenStatusEnabled, true, false).Where(clause.Eq{Column: "group", Value: ""}).First(candidate).Error
			if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
				return Sub2APISubject{}, nil, err
			}
			if err == nil {
				token = candidate
			}
		}
	}
	subject := Sub2APISubject{UserID: userID}
	payer := user
	tokens := []*model.Token{token}
	if token != nil && token.WorkspaceUserID > 0 {
		if err := model.ValidateWorkspaceToken(token); err != nil {
			return subject, nil, err
		}
		subject = Sub2APISubject{UserID: token.WorkspaceUserID, WorkspaceTeamID: token.WorkspaceTeamID}
		payer, err = model.GetUserById(token.UserId, false)
		if err != nil {
			return subject, nil, err
		}
		if payer.Status != common.UserStatusEnabled {
			return subject, nil, errSub2APIEnrollmentIneligible
		}
	} else {
		// Personal native API keys may have different group/model restrictions
		// from the workspace default key. Prepare the union of their authorized
		// routes; never reuse a team's payer-owned member key as personal access.
		var personalTokens []*model.Token
		if err := model.DB.Where("user_id = ? AND workspace_user_id = 0 AND status = ?", userID, common.TokenStatusEnabled).
			Where("expired_time = -1 OR expired_time >= ?", time.Now().Unix()).Find(&personalTokens).Error; err != nil {
			return subject, nil, err
		}
		tokens = personalTokens
		if token == nil {
			// A newly registered user's standard personal key is created lazily.
			// Retain preparation of its default route before that first use.
			tokens = append(tokens, nil)
		}
	}
	poolIDs := make([]int, 0, len(cfg.Pools))
	for _, pool := range cfg.Pools {
		poolIDs = append(poolIDs, pool.ChannelID)
	}
	var abilities []model.Ability
	if err := model.DB.Where("channel_id IN ? AND enabled = ?", poolIDs, true).Find(&abilities).Error; err != nil {
		return subject, nil, err
	}
	var channels []model.Channel
	if err := model.DB.Where("id IN ? AND type = ? AND status = ?", poolIDs, constant.ChannelTypeSub2API, common.ChannelStatusEnabled).Find(&channels).Error; err != nil {
		return subject, nil, err
	}
	routes := make(map[int]string, len(channels))
	for _, channel := range channels {
		routes[channel.Id] = channel.GetBaseURL()
	}
	allowed := make(map[int]bool)
	for _, token := range tokens {
		groups := []string{payer.Group}
		if token != nil {
			if token.Status != common.TokenStatusEnabled || (token.ExpiredTime != -1 && token.ExpiredTime < time.Now().Unix()) {
				continue
			}
			if err := model.ValidateWorkspaceToken(token); err != nil {
				if errors.Is(err, model.ErrTokenInvalid) {
					continue
				}
				return subject, nil, err
			}
			if token.Group == "auto" {
				if !GroupInUserUsableGroups(payer.Group, "auto") {
					continue
				}
				auto, err := token.GetAutoGroups()
				if err != nil {
					return subject, nil, err
				}
				if auto == nil {
					groups = GetUserAutoGroup(payer.Group)
				} else {
					groups = FilterUserTokenAutoGroups(payer.Group, auto)
				}
			} else if token.Group != "" {
				if !IsUserSelectableGroup(payer.Group, token.Group) {
					continue
				}
				groups = []string{token.Group}
			}
		}
		for _, ability := range abilities {
			if !slices.Contains(groups, ability.Group) {
				continue
			}
			if token != nil && token.IsModelLimitsEnabled() {
				limits := token.GetModelLimitsMap()
				if !limits[ability.Model] && !limits[ratio_setting.RoutingMatchModelName(ability.Model)] {
					continue
				}
			}
			allowed[ability.ChannelId] = true
		}
	}
	pools := make([]Sub2APIPool, 0)
	for _, pool := range cfg.Pools {
		if allowed[pool.ChannelID] && routes[pool.ChannelID] == pool.BaseURL {
			pools = append(pools, pool)
		}
	}
	return subject, pools, nil
}

// ReconcileSub2APIEnrollments reads committed customer state only. It performs
// no upstream calls, schema changes, token creation or customer ledger writes.
func ReconcileSub2APIEnrollments(ctx context.Context) error {
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return err
	}
	if !cfg.Provision.Enabled || cfg.Provision.Mode != "prewarmed" {
		return nil
	}
	lastID := 0
	for {
		var users []model.User
		if err := model.DB.WithContext(ctx).Select("id").Where("id > ? AND status = ?", lastID, common.UserStatusEnabled).Order("id").Limit(100).Find(&users).Error; err != nil {
			return errors.New("Sub2API enrollment customer scan failed")
		}
		if len(users) == 0 {
			return nil
		}
		for _, user := range users {
			if err := ctx.Err(); err != nil {
				return err
			}
			lastID = user.Id
			for _, scope := range []string{"personal", "team"} {
				if scope == "team" {
					member, team, err := model.FindWorkspaceMembership(user.Id)
					if err != nil {
						return errors.New("Sub2API enrollment membership scan failed")
					}
					if member == nil || team == nil || member.Status != common.UserStatusEnabled {
						continue
					}
				}
				subject, pools, err := sub2APIWorkspacePools(cfg, user.Id, scope)
				if err != nil {
					if errors.Is(err, errSub2APIEnrollmentIneligible) || errors.Is(err, model.ErrTokenInvalid) || errors.Is(err, model.ErrWorkspaceAccess) || errors.Is(err, gorm.ErrRecordNotFound) {
						continue
					}
					return errors.New("Sub2API enrollment authorization scan failed")
				} // Disabled/revoked keys are not enrolled.
				for _, pool := range pools {
					if err := QueueSub2APIIdentity(cfg, subject, pool.ChannelID); err != nil {
						return err
					}
				}
			}
		}
	}
}

func StartSub2APIEnrollmentTask() context.CancelFunc {
	ctx, cancel := context.WithCancel(context.Background())
	if !Sub2APIDriverEnabled() {
		return cancel
	}
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		defer func() {
			sub2APIEnrollmentScan.Lock()
			sub2APIEnrollmentScan.Status = "stopped"
			sub2APIEnrollmentScan.Unlock()
		}()
		for {
			err := ReconcileSub2APIEnrollments(ctx)
			sub2APIEnrollmentScan.Lock()
			if err == nil {
				sub2APIEnrollmentScan.LastSuccess, sub2APIEnrollmentScan.Status = time.Now().UTC(), "running"
			} else {
				sub2APIEnrollmentScan.Status = "error"
			}
			sub2APIEnrollmentScan.Unlock()
			if err != nil {
				common.SysLog("Sub2API enrollment reconciliation unavailable; customer transactions remain independent")
			}
			select {
			case <-ticker.C:
			case <-ctx.Done():
				return
			}
		}
	}()
	return cancel
}

func readSub2APIEnrollmentJSON(path string, value any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, (16<<20)+1))
	if err != nil || len(raw) > 16<<20 {
		return errors.New("invalid enrollment state")
	}
	return common.Unmarshal(raw, value)
}

func GetSub2APIEnrollmentStatus(userID int, scope string) Sub2APIEnrollmentStatus {
	result := Sub2APIEnrollmentStatus{Enabled: Sub2APIDriverEnabled(), Status: "disabled", Reason: "disabled"}
	if !result.Enabled {
		return result
	}
	result.Status, result.Reason = "error", "not_configured"
	sub2APIEnrollmentScan.RLock()
	result.ScannerStatus = sub2APIEnrollmentScan.Status
	if !sub2APIEnrollmentScan.LastSuccess.IsZero() {
		last := sub2APIEnrollmentScan.LastSuccess
		result.ScannerLastSuccess = &last
	}
	sub2APIEnrollmentScan.RUnlock()
	cfg, err := LoadSub2APIConfig()
	stateDir := strings.TrimSpace(os.Getenv("REALYU_SUB2API_STATE_DIR"))
	if err != nil || stateDir == "" {
		return result
	}
	subject, pools, err := sub2APIWorkspacePools(cfg, userID, scope)
	if err != nil {
		result.Reason = "scope_unavailable"
		return result
	}
	if len(pools) == 0 {
		result.Reason = "no_authorized_pool"
		return result
	}
	for _, pool := range pools {
		if err := QueueSub2APIIdentity(cfg, subject, pool.ChannelID); err != nil {
			return result
		}
	}
	var progress struct {
		Version      int    `json:"version"`
		ConfigSHA256 string `json:"config_sha256"`
		Tasks        map[string]struct {
			Status string `json:"status"`
		} `json:"tasks"`
	}
	stateErr := readSub2APIEnrollmentJSON(filepath.Join(stateDir, cfg.digest+".json"), &progress)
	if stateErr != nil && !os.IsNotExist(stateErr) {
		result.Reason = "state_unavailable"
		return result
	}
	if stateErr == nil && (progress.Version != 1 || progress.ConfigSHA256 != cfg.digest) {
		result.Reason = "state_unavailable"
		return result
	}
	for _, pool := range pools {
		key := fmt.Sprintf("%d:%d:%d", subject.UserID, subject.WorkspaceTeamID, pool.ChannelID)
		switch progress.Tasks[key].Status {
		case "DONE":
			result.ReadyCount++
		case "BLOCKED", "FAILED":
			result.ErrorCount++
		default:
			result.PendingCount++
		}
	}
	var heartbeat struct {
		ConfigSHA256 string    `json:"config_sha256"`
		LastSeen     time.Time `json:"last_seen"`
		Status       string    `json:"status"`
		CreditStatus string    `json:"credit_status"`
	}
	heartbeatErr := readSub2APIEnrollmentJSON(filepath.Join(stateDir, "heartbeat.json"), &heartbeat)
	if heartbeatErr == nil && heartbeat.ConfigSHA256 == cfg.digest && !heartbeat.LastSeen.IsZero() {
		result.WorkerLastSeen = &heartbeat.LastSeen
	}
	if result.ErrorCount > 0 {
		result.Reason = "operator_action_required"
		return result
	}
	if heartbeatErr == nil && heartbeat.ConfigSHA256 == cfg.digest && (heartbeat.CreditStatus == "blocked" || heartbeat.CreditStatus == "error") {
		result.Reason = "operator_action_required"
		return result
	}
	if result.PendingCount == 0 {
		result.Status, result.Reason = "ready", "provisioned"
		return result
	}
	result.Status, result.Reason = "pending", "queued"
	if result.WorkerLastSeen == nil || time.Since(*result.WorkerLastSeen) > 90*time.Second || time.Until(*result.WorkerLastSeen) > time.Minute {
		result.Status, result.Reason = "error", "worker_unavailable"
	} else if heartbeat.Status == "blocked" || heartbeat.Status == "error" {
		result.Status, result.Reason = "error", "operator_action_required"
	}
	return result
}
