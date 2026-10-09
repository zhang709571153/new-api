package model

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"slices"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

// Member usage belongs to an immutable subscription window. Editing the team's
// displayed usage never changes these rows; advancing the shared window makes
// every member start at zero while retaining late settlements in their old week.
type WorkspaceMemberWeeklyUsage struct {
	TeamID         int   `gorm:"primaryKey;autoIncrement:false"`
	UserID         int   `gorm:"primaryKey;autoIncrement:false"`
	SubscriptionID int   `gorm:"primaryKey;autoIncrement:false"`
	WeeklyResetAt  int64 `gorm:"primaryKey;autoIncrement:false;type:bigint"`
	UsedQuota      int64 `gorm:"type:bigint;not null;default:0"`
	// Historical display only. It never participates in admission, settlement,
	// refunds, persistent member limits, or the team's subscription counter.
	OpeningUsedQuota int64 `gorm:"type:bigint;not null;default:0"`
}

func WorkspaceMemberWeeklyQuota(member *WorkspaceMember, token *Token) int64 {
	if member.WeeklyQuota != nil {
		return max(int64(0), *member.WeeklyQuota)
	}
	return int64(max(0, token.RemainQuota))
}

// Explicit, idempotent bootstrap for a drained deployment. Preserve the old
// remaining allowance as the first weekly cap; never infer or enlarge it from
// lifetime usage. NULL distinguishes untouched rows from a deliberate zero cap.
func InitializeWorkspaceMemberWeeklyQuotas() (int64, error) {
	var initialized int64
	err := DB.Transaction(func(tx *gorm.DB) error {
		var teams []WorkspaceTeam
		if err := tx.Where("funding_version > 0").Order("id").Find(&teams).Error; err != nil {
			return err
		}
		for _, team := range teams {
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ?", team.ID).Error; err != nil {
				return err
			}
			var members []WorkspaceMember
			if err := tx.Where("team_id = ? AND weekly_quota IS NULL", team.ID).Find(&members).Error; err != nil {
				return err
			}
			for _, member := range members {
				var token Token
				if err := tx.Unscoped().First(&token, member.TokenID).Error; err != nil {
					return err
				}
				updated := tx.Model(&member).Where("weekly_quota IS NULL").Update("weekly_quota", max(0, token.RemainQuota))
				if updated.Error != nil {
					return updated.Error
				}
				initialized += updated.RowsAffected
			}
		}
		return nil
	})
	return initialized, err
}

func bindWorkspaceMemberFundingTx(tx *gorm.DB, record *SubscriptionPreConsumeRecord, tokenID int, isNew bool) error {
	if record.WorkspaceTeamID <= 0 || tokenID <= 0 {
		return nil
	}
	if !isNew {
		if record.WorkspaceTokenID > 0 && record.WorkspaceTokenID != tokenID {
			return errors.New("subscription funding member identity mismatch")
		}
		return nil
	}
	if record.WeeklyResetAt <= 0 {
		return errors.New("subscription quota insufficient: team weekly allowance not configured")
	}
	var token Token
	if err := tx.First(&token, "id = ? AND user_id = ? AND workspace_user_id > 0", tokenID, record.UserId).Error; err != nil {
		return ErrWorkspaceAccess
	}
	var member WorkspaceMember
	if err := tx.First(&member, "team_id = ? AND user_id = ? AND token_id = ?", record.WorkspaceTeamID, token.WorkspaceUserID, tokenID).Error; err != nil {
		return ErrWorkspaceAccess
	}
	if member.WeeklyQuota == nil {
		if err := tx.Model(&member).Where("weekly_quota IS NULL").Update("weekly_quota", max(0, token.RemainQuota)).Error; err != nil {
			return err
		}
	}
	record.WorkspaceTokenID = tokenID
	record.WorkspaceMemberUserID = member.UserID
	return nil
}

func updateWorkspaceMemberWeeklyUsageTx(tx *gorm.DB, record *SubscriptionPreConsumeRecord, delta int64, action string, currentResetAt int64) error {
	if record.WorkspaceTeamID <= 0 || record.WorkspaceMemberUserID <= 0 || record.UserSubscriptionId <= 0 || record.WeeklyResetAt <= 0 {
		return nil
	}
	row := WorkspaceMemberWeeklyUsage{TeamID: record.WorkspaceTeamID, UserID: record.WorkspaceMemberUserID, SubscriptionID: record.UserSubscriptionId, WeeklyResetAt: record.WeeklyResetAt}
	// Team funding already holds the account lock, serializing first insertion
	// and updates even when several member requests settle concurrently.
	err := lockForUpdate(tx).Where("team_id = ? AND user_id = ? AND subscription_id = ? AND weekly_reset_at = ?", row.TeamID, row.UserID, row.SubscriptionID, row.WeeklyResetAt).First(&row).Error
	isNew := errors.Is(err, gorm.ErrRecordNotFound)
	if err != nil && !isNew {
		return err
	}
	var token Token
	if err := lockForUpdate(tx.Unscoped()).First(&token, record.WorkspaceTokenID).Error; err != nil {
		return err
	}
	var member WorkspaceMember
	memberErr := tx.Where("team_id = ? AND user_id = ? AND token_id = ?", record.WorkspaceTeamID, record.WorkspaceMemberUserID, record.WorkspaceTokenID).First(&member).Error
	if memberErr != nil && !errors.Is(memberErr, gorm.ErrRecordNotFound) {
		return memberErr
	}
	if action == "reserve" {
		validStatus := token.Status == common.TokenStatusEnabled || token.Status == common.TokenStatusExhausted
		expired := token.ExpiredTime != -1 && token.ExpiredTime < common.GetTimestamp()
		if memberErr != nil || member.Status != common.UserStatusEnabled || !validStatus || expired || token.DeletedAt.Valid {
			return ErrWorkspaceAccess
		}
		if record.WorkspaceMemberUserID != record.UserId {
			remaining := WorkspaceMemberWeeklyQuota(&member, &token) - row.UsedQuota
			if delta >= 0 && (remaining <= 0 || delta > remaining) {
				return errors.New("subscription quota insufficient: member weekly allowance exhausted")
			}
		}
	}
	if delta > common.MaxWalletQuota-row.UsedQuota {
		return errors.New("member weekly usage out of range")
	}
	row.UsedQuota = max(int64(0), row.UsedQuota+delta)
	if delta != 0 {
		if delta > common.MaxWalletQuota-int64(token.UsedQuota) {
			return errors.New("member token usage out of range")
		}
		values := map[string]any{"used_quota": max(int64(0), int64(token.UsedQuota)+delta), "accessed_time": common.GetTimestamp()}
		if memberErr == nil && record.WeeklyResetAt == currentResetAt && record.WorkspaceMemberUserID != record.UserId {
			values["remain_quota"] = max(int64(0), WorkspaceMemberWeeklyQuota(&member, &token)-row.UsedQuota)
		}
		if err := tx.Unscoped().Model(&token).Updates(values).Error; err != nil {
			return err
		}
	}
	if isNew {
		return tx.Create(&row).Error
	}
	return tx.Model(&row).Update("used_quota", row.UsedQuota).Error
}

func GetWorkspaceMemberWeeklyUsage(teamID int, subscriptions []SubscriptionSummary) (map[int]int64, int64, error) {
	result := make(map[int]int64)
	rows, resetAt, err := GetWorkspaceMemberWeeklyUsageSnapshot(teamID, subscriptions)
	for userID, row := range rows {
		result[userID] = row.UsedQuota
	}
	return result, resetAt, err
}

func GetWorkspaceMemberWeeklyUsageSnapshot(teamID int, subscriptions []SubscriptionSummary) (map[int]WorkspaceMemberWeeklyUsage, int64, error) {
	result := make(map[int]WorkspaceMemberWeeklyUsage)
	if teamID <= 0 || len(subscriptions) != 1 || subscriptions[0].Subscription == nil {
		return result, 0, nil
	}
	sub := *subscriptions[0].Subscription
	if sub.WeeklyAmount <= 0 || sub.WorkspaceTeamID != teamID {
		return result, 0, nil
	}
	var rows []WorkspaceMemberWeeklyUsage
	if err := DB.Where("team_id = ? AND subscription_id = ? AND weekly_reset_at = ?", teamID, sub.Id, sub.WeeklyResetAt).Find(&rows).Error; err != nil {
		return nil, 0, err
	}
	for _, row := range rows {
		result[row.UserID] = row
	}
	return result, sub.WeeklyResetAt, nil
}

// These inputs are generated from an immutable, verified cold backup by the
// bounded maintenance tool. They are deliberately not exposed by an HTTP API.
type WorkspaceMemberOpeningEntry struct {
	UserID           int   `json:"user_id"`
	TokenID          int   `json:"token_id"`
	OpeningUsedQuota int64 `json:"opening_used_quota"`
}

type WorkspaceMemberOpeningWindow struct {
	TeamID         int                           `json:"team_id"`
	OwnerUserID    int                           `json:"owner_user_id"`
	SubscriptionID int                           `json:"subscription_id"`
	WeeklyResetAt  int64                         `json:"weekly_reset_at"`
	Members        []WorkspaceMemberOpeningEntry `json:"members"`
}

type WorkspaceMemberOpeningImport struct {
	OperationID  string                         `json:"operation_id"`
	SourceSHA256 string                         `json:"source_sha256"`
	Windows      []WorkspaceMemberOpeningWindow `json:"windows"`
}

// Only the standalone importer creates this audit table; normal gateway
// startup and relay requests do not need to create or access receipts.
type WorkspaceMemberOpeningReceipt struct {
	OperationID  string `gorm:"primaryKey;size:80"`
	SourceSHA256 string `gorm:"size:64;not null"`
	InputSHA256  string `gorm:"size:64;not null"`
	InputJSON    string `gorm:"type:text;not null"`
	CreatedAt    int64  `gorm:"type:bigint;not null"`
}

// BackfillWorkspaceMemberWeeklyOpening is idempotent even after later requests
// or a rollover. A new import, however, must still address the exact live
// subscription window and membership set. Prepare the receipt schema before
// calling: DDL inside this transaction would implicitly commit on MySQL.
func BackfillWorkspaceMemberWeeklyOpening(input WorkspaceMemberOpeningImport, beforeCommit ...func() error) (bool, error) {
	if len(input.OperationID) == 0 || len(input.OperationID) > 80 || len(input.Windows) == 0 || len(input.SourceSHA256) != 64 {
		return false, errors.New("invalid member opening import")
	}
	if _, err := hex.DecodeString(input.SourceSHA256); err != nil {
		return false, errors.New("invalid member opening source hash")
	}
	input.Windows = slices.Clone(input.Windows)
	slices.SortFunc(input.Windows, func(a, b WorkspaceMemberOpeningWindow) int { return a.TeamID - b.TeamID })
	for i := range input.Windows {
		window := &input.Windows[i]
		if window.TeamID <= 0 || window.OwnerUserID <= 0 || window.SubscriptionID <= 0 || window.WeeklyResetAt <= 0 || len(window.Members) == 0 || (i > 0 && input.Windows[i-1].TeamID == window.TeamID) {
			return false, errors.New("invalid member opening window")
		}
		window.Members = slices.Clone(window.Members)
		slices.SortFunc(window.Members, func(a, b WorkspaceMemberOpeningEntry) int { return a.UserID - b.UserID })
		for j, member := range window.Members {
			if member.UserID <= 0 || member.TokenID <= 0 || member.OpeningUsedQuota < 0 || member.OpeningUsedQuota > common.MaxWalletQuota || (j > 0 && window.Members[j-1].UserID == member.UserID) {
				return false, errors.New("invalid member opening baseline")
			}
		}
	}
	encoded, err := common.Marshal(input)
	if err != nil {
		return false, err
	}
	digest := sha256.Sum256(encoded)
	fingerprint := hex.EncodeToString(digest[:])
	applied := false
	err = DB.Transaction(func(tx *gorm.DB) error {
		// Lock every team first, then accounts, matching join/provision/funding.
		teams := make(map[int]WorkspaceTeam, len(input.Windows))
		for _, window := range input.Windows {
			var team WorkspaceTeam
			if err := lockForUpdate(tx).First(&team, window.TeamID).Error; err != nil {
				return err
			}
			teams[team.ID] = team
		}
		for _, window := range input.Windows {
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ?", window.TeamID).Error; err != nil {
				return err
			}
		}
		var receipt WorkspaceMemberOpeningReceipt
		err := tx.First(&receipt, "operation_id = ?", input.OperationID).Error
		if err == nil {
			if receipt.InputSHA256 != fingerprint || receipt.SourceSHA256 != input.SourceSHA256 || receipt.InputJSON != string(encoded) {
				return errors.New("member opening receipt input mismatch")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		now := getDBTimestampTx(tx)
		for _, window := range input.Windows {
			team := teams[window.TeamID]
			var account WorkspaceTeamAccount
			if err := tx.First(&account, "team_id = ?", team.ID).Error; err != nil {
				return err
			}
			if team.OwnerUserID != window.OwnerUserID || team.FundingVersion <= 0 || account.ClosedAt != 0 {
				return errors.New("member opening team changed")
			}
			var subscriptions []UserSubscription
			if err := lockForUpdate(tx).Where("workspace_team_id = ? AND status = ? AND end_time > ?", team.ID, "active", now).Find(&subscriptions).Error; err != nil {
				return err
			}
			if len(subscriptions) != 1 {
				return errors.New("member opening subscription changed")
			}
			sub := subscriptions[0]
			if sub.Id != window.SubscriptionID || sub.UserId != team.OwnerUserID || sub.StartTime > now || sub.WeeklyAmount <= 0 || sub.WeeklyResetAt != window.WeeklyResetAt || sub.WeeklyResetAt <= now {
				return errors.New("member opening window changed or elapsed")
			}
			var members []WorkspaceMember
			if err := tx.Where("team_id = ?", team.ID).Order("user_id").Find(&members).Error; err != nil {
				return err
			}
			if len(members) != len(window.Members) {
				return errors.New("member opening membership changed")
			}
			for i, member := range members {
				entry := window.Members[i]
				if entry.UserID != member.UserID || entry.TokenID != member.TokenID || member.WeeklyQuota == nil {
					return errors.New("member opening member or key changed")
				}
				row := WorkspaceMemberWeeklyUsage{TeamID: team.ID, UserID: member.UserID, SubscriptionID: sub.Id, WeeklyResetAt: sub.WeeklyResetAt}
				err := tx.Where("team_id = ? AND user_id = ? AND subscription_id = ? AND weekly_reset_at = ?", row.TeamID, row.UserID, row.SubscriptionID, row.WeeklyResetAt).First(&row).Error
				isNew := errors.Is(err, gorm.ErrRecordNotFound)
				if err != nil && !isNew {
					return err
				}
				if row.OpeningUsedQuota != 0 || row.UsedQuota < 0 || row.UsedQuota > common.MaxWalletQuota-entry.OpeningUsedQuota || *member.WeeklyQuota > common.MaxWalletQuota-entry.OpeningUsedQuota {
					return errors.New("member opening already set or quota out of range")
				}
				row.OpeningUsedQuota = entry.OpeningUsedQuota
				if isNew {
					err = tx.Create(&row).Error
				} else {
					err = tx.Model(&row).Update("opening_used_quota", entry.OpeningUsedQuota).Error
				}
				if err != nil {
					return err
				}
			}
		}
		if err := tx.Create(&WorkspaceMemberOpeningReceipt{OperationID: input.OperationID, SourceSHA256: input.SourceSHA256, InputSHA256: fingerprint, InputJSON: string(encoded), CreatedAt: now}).Error; err != nil {
			return err
		}
		for _, window := range input.Windows {
			if window.WeeklyResetAt <= common.GetTimestamp() {
				return errors.New("member opening window elapsed before commit")
			}
		}
		for _, guard := range beforeCommit {
			if err := guard(); err != nil {
				return err
			}
		}
		applied = true
		return nil
	})
	return applied && err == nil, err
}
