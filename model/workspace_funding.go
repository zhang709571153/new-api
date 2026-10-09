package model

import (
	"errors"
	"slices"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

var ErrWorkspaceTeamSubscriptionConflict = errors.New("subscription quota insufficient: multiple active team subscriptions")

// WorkspaceTeamAccount is a durable funding principal. It survives dissolution
// so delayed settlement/refunds and historical subscriptions retain their owner.
// It is never a login identity and never borrows a user's wallet.
type WorkspaceTeamAccount struct {
	TeamID      int   `json:"team_id" gorm:"primaryKey;autoIncrement:false"`
	OwnerUserID int   `json:"owner_user_id" gorm:"index"`
	Quota       int64 `json:"-" gorm:"type:bigint;not null;default:0"`
	ClosedAt    int64 `json:"closed_at" gorm:"type:bigint;not null;default:0"`
}

// A migration receipt makes retrying the exact transfer a no-op. No keys or
// mutable balance snapshots are placed in the receipt.
type WorkspaceFundingMigration struct {
	TeamID          int `gorm:"primaryKey;autoIncrement:false"`
	OwnerUserID     int
	SubscriptionIDs string `gorm:"type:text"`
	WalletQuota     int64  `gorm:"type:bigint"`
	CreatedAt       int64
}

// MigrateWorkspaceFunding is explicit and atomic; startup never moves assets.
// The operator must drain old requests before calling it. Unsettled durable
// reservations are an additional fail-closed check, not proof of an idle server.
func MigrateWorkspaceFunding(ownerID, teamID int, subscriptionIDs []int, walletQuota int64) error {
	if ownerID <= 0 || teamID <= 0 || walletQuota != 0 {
		return ErrWorkspaceAccess
	}
	ids := append([]int(nil), subscriptionIDs...)
	slices.Sort(ids)
	for i, id := range ids {
		if id <= 0 || i > 0 && id == ids[i-1] {
			return errors.New("invalid subscription selection")
		}
	}
	encoded, err := common.Marshal(ids)
	if err != nil {
		return err
	}
	err = DB.Transaction(func(tx *gorm.DB) error {
		var team WorkspaceTeam
		if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ?", teamID, ownerID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		var receipt WorkspaceFundingMigration
		err := lockForUpdate(tx).First(&receipt, teamID).Error
		if err == nil {
			if receipt.OwnerUserID != ownerID || receipt.SubscriptionIDs != string(encoded) || receipt.WalletQuota != walletQuota {
				return errors.New("migration already applied with different inputs")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if team.FundingVersion != 0 {
			return errors.New("team funding is already independent")
		}
		var owner User
		if err := lockForUpdate(tx).First(&owner, ownerID).Error; err != nil {
			return err
		}
		var pending int64
		if err := tx.Model(&SubscriptionPreConsumeRecord{}).Where("user_id = ? AND status = ?", ownerID, "consumed").Count(&pending).Error; err != nil {
			return err
		}
		if pending > 0 {
			return errors.New("unsettled reservations remain; drain and reconcile before migration")
		}
		var active int64
		if err := tx.Model(&UserSubscription{}).Where("workspace_team_id = ? AND status = ? AND end_time > ?", teamID, "active", getDBTimestampTx(tx)).Count(&active).Error; err != nil {
			return err
		}
		if active > 1 {
			return ErrWorkspaceTeamSubscriptionConflict
		}
		for _, id := range ids {
			var sub UserSubscription
			if err := lockForUpdate(tx).First(&sub, "id = ? AND user_id = ? AND workspace_team_id = 0", id, ownerID).Error; err != nil {
				return errors.New("subscription does not belong to this personal account")
			}
			if sub.UpgradeGroup != "" || sub.DowngradeGroup != "" {
				return errors.New("group-changing subscription requires a separate migration")
			}
			if sub.Status == "active" && sub.EndTime > getDBTimestampTx(tx) {
				active++
				if active > 1 {
					return ErrWorkspaceTeamSubscriptionConflict
				}
			}
			// UpdateColumn preserves all amounts, windows, timestamps and reservations.
			if err := tx.Model(&sub).UpdateColumns(map[string]any{"workspace_team_id": teamID, "allow_wallet_overflow": false}).Error; err != nil {
				return err
			}
		}
		var personalActive int64
		if err := tx.Model(&UserSubscription{}).Where("workspace_team_id = 0 AND user_id = ? AND status = ? AND end_time > ?", ownerID, "active", getDBTimestampTx(tx)).Count(&personalActive).Error; err != nil {
			return err
		}
		if personalActive > 1 {
			return ErrSubscriptionConflict
		}
		if err := tx.Create(&WorkspaceTeamAccount{TeamID: teamID, OwnerUserID: ownerID, Quota: walletQuota}).Error; err != nil {
			return err
		}
		// Old owner/member keys keep both their secret and token ID. Only the
		// personal singleton mapping is repaired to a separate personal key.
		if _, err := workspacePersonalToken(tx, &owner, true); err != nil {
			return err
		}
		if err := tx.Model(&team).Update("funding_version", 1).Error; err != nil {
			return err
		}
		return tx.Create(&WorkspaceFundingMigration{TeamID: teamID, OwnerUserID: ownerID, SubscriptionIDs: string(encoded), WalletQuota: walletQuota, CreatedAt: common.GetTimestamp()}).Error
	})
	if err == nil {
		_ = invalidateUserCache(ownerID)
	}
	return err
}

func WorkspaceTeamFunding(team *WorkspaceTeam) (float64, float64, []SubscriptionSummary, error) {
	if team.FundingVersion == 0 {
		balance, subs, err := WorkspaceFunding(team.OwnerUserID)
		owner, accountErr := GetWorkspaceAccountSnapshot(team.OwnerUserID)
		if err != nil {
			return 0, 0, nil, err
		}
		if accountErr != nil {
			return 0, 0, nil, accountErr
		}
		return balance, float64(max(0, owner.Quota)) / common.QuotaPerUnit, subs, nil
	}
	var account WorkspaceTeamAccount
	if err := DB.First(&account, team.ID).Error; err != nil {
		return 0, 0, nil, err
	}
	var subs []UserSubscription
	now := GetDBTimestamp()
	if err := DB.Where("workspace_team_id = ? AND status = ? AND end_time > ?", team.ID, "active", now).Order("end_time desc, id desc").Find(&subs).Error; err != nil {
		return 0, 0, nil, err
	}
	if len(subs) > 1 {
		// Preserve the conflicting rows for diagnosis, while balance and key
		// identity reads remain available. Never sum ambiguous entitlements.
		return 0, 0, buildSubscriptionSummaries(subs), nil
	}
	if len(subs) == 1 && subs[0].StartTime > now {
		return 0, 0, []SubscriptionSummary{}, nil
	}
	summaries := buildSubscriptionSummaries(subs)
	// Retain historical account balances for reconciliation, but independent
	// teams have no spendable PAYGO balance.
	available := float64(0)
	for _, item := range summaries {
		s := item.Subscription
		if s.WeeklyAmount <= 0 {
			continue
		}
		remaining := s.AvailableQuota()
		available += float64(remaining) / common.QuotaPerUnit
	}
	return available, 0, summaries, nil
}
