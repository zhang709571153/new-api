package model

import (
	"errors"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

var ErrSubscriptionConflict = errors.New("subscription quota insufficient: multiple active subscriptions")

// Paid CNY plans and administrator grants consume allowance before permanent cash.
func HasActiveSubscriptionFirstFunding(userId int) (bool, error) {
	var subs []UserSubscription
	if err := DB.Where("workspace_team_id = 0 AND user_id = ? AND status = ? AND end_time > ?", userId, "active", GetDBTimestamp()).Limit(2).Find(&subs).Error; err != nil {
		return false, err
	}
	if len(subs) > 1 {
		return false, ErrSubscriptionConflict
	}
	return len(subs) == 1 && ((subs[0].Source == "cny_order" && subs[0].PurchasePriceCents > 0) || subs[0].Source == "workspace_admin"), nil
}

type SubscriptionFirstFunding struct {
	Record            SubscriptionPreConsumeRecord
	Subscription      UserSubscription
	AppliedDelta      int64
	SubscriptionDelta int64
	WalletDelta       int64
}

// UpdateSubscriptionFirstFunding is an absolute, request-idempotent reservation
// and settlement ledger. Cash and allowance move in one database transaction.
// The old cache-authoritative/batched wallet mode cannot safely share this
// transaction and is deliberately rejected rather than silently overspending.
func UpdateSubscriptionFirstFunding(userId int, requestID string, target int64, action string) (*SubscriptionFirstFunding, error) {
	return updateScopedSubscriptionFunding(userId, 0, requestID, target, action)
}

func UpdateWorkspaceTeamFunding(userID, teamID int, requestID string, target int64, action string, tokenIDs ...int) (*SubscriptionFirstFunding, error) {
	if teamID <= 0 {
		return nil, ErrWorkspaceAccess
	}
	return updateScopedSubscriptionFunding(userID, teamID, requestID, target, action, tokenIDs...)
}

func updateScopedSubscriptionFunding(userId, teamID int, requestID string, target int64, action string, tokenIDs ...int) (*SubscriptionFirstFunding, error) {
	if err := EnsureCNYBillingLedgerMode(); err != nil {
		return nil, err
	}
	if userId <= 0 || strings.TrimSpace(requestID) == "" || target < -common.MaxQuota || (target < 0 && action != "adjust") || target > common.MaxQuota || (action != "reserve" && action != "settle" && action != "refund" && action != "adjust" && action != "reconcile") {
		return nil, errors.New("invalid subscription funding adjustment")
	}
	result := &SubscriptionFirstFunding{}
	tokenID := 0
	if len(tokenIDs) > 0 {
		tokenID = tokenIDs[0]
	}
	err := DB.Transaction(func(tx *gorm.DB) error {
		var user User
		var account WorkspaceTeamAccount
		var wallet int64
		if teamID > 0 {
			if err := lockForUpdate(tx).First(&account, "team_id = ? AND owner_user_id = ?", teamID, userId).Error; err != nil {
				return err
			}
			if action == "reserve" && account.ClosedAt > 0 {
				return ErrWorkspaceAccess
			}
			wallet = account.Quota
		} else {
			if err := lockForUpdate(tx).First(&user, userId).Error; err != nil {
				return err
			}
			wallet = int64(user.Quota)
		}
		record := &result.Record
		err := lockForUpdate(tx).Where("request_id = ?", requestID).First(record).Error
		isNew := errors.Is(err, gorm.ErrRecordNotFound)
		if err != nil && !isNew {
			return err
		}
		sub := &result.Subscription
		now := getDBTimestampTx(tx)
		var teamSubscriptions []UserSubscription
		if teamID == 0 && action == "reserve" {
			var active int64
			if err := tx.Model(&UserSubscription{}).Where("workspace_team_id = 0 AND user_id = ? AND status = ? AND end_time > ?", userId, "active", now).Count(&active).Error; err != nil {
				return err
			}
			if active > 1 {
				return ErrSubscriptionConflict
			}
		}
		if teamID > 0 && action == "reserve" {
			// The account lock serializes this check with team grants. Future
			// subscriptions also count, so a second queued contract is a conflict.
			if err := lockForUpdate(tx).Where("workspace_team_id = ? AND status = ? AND end_time > ?", teamID, "active", now).Order("id").Limit(2).Find(&teamSubscriptions).Error; err != nil {
				return err
			}
			if len(teamSubscriptions) > 1 {
				return ErrWorkspaceTeamSubscriptionConflict
			}
		}
		if isNew {
			if action != "reserve" {
				return errors.New("subscription funding reservation missing")
			}
			query := lockForUpdate(tx).Where("user_id = ? AND workspace_team_id = ? AND status = ? AND end_time > ?", userId, teamID, "active", now)
			if teamID > 0 {
				if len(teamSubscriptions) == 0 {
					return errors.New("subscription quota insufficient: no active team subscription")
				}
				*sub = teamSubscriptions[0]
				if sub.UserId != userId {
					return ErrWorkspaceAccess
				}
				sub.RefreshWeeklyWindow(now)
			} else {
				query = query.Where("((source = ? AND purchase_price_cents > 0) OR source = ?)", "cny_order", "workspace_admin")
				if err := query.Order("end_time asc, id asc").First(sub).Error; err != nil {
					return err
				}
				sub.RefreshWeeklyWindow(now)
			}
			*record = SubscriptionPreConsumeRecord{WorkspaceTeamID: teamID, RequestId: requestID, UserId: userId, UserSubscriptionId: sub.Id, WeeklyResetAt: sub.WeeklyResetAt, FundingMode: "subscription_first", Status: "consumed"}
			if err := bindWorkspaceMemberFundingTx(tx, record, tokenID, true); err != nil {
				return err
			}
		} else {
			if record.UserId != userId || record.WorkspaceTeamID != teamID || record.FundingMode != "subscription_first" {
				return errors.New("subscription funding identity mismatch")
			}
			if err := bindWorkspaceMemberFundingTx(tx, record, tokenID, false); err != nil {
				return err
			}
			if record.UserSubscriptionId > 0 {
				if err := lockForUpdate(tx).First(sub, record.UserSubscriptionId).Error; err != nil {
					return err
				}
			}
			sub.RefreshWeeklyWindow(now)
			if record.Status == "refunded" {
				if action == "refund" || (action == "reconcile" && target == 0) {
					return nil
				}
				return errors.New("subscription funding already refunded")
			}
			if record.Status == "settled" && action != "adjust" && action != "reconcile" {
				if action == "settle" && target == record.PreConsumed+record.WalletPreConsumed {
					return nil
				}
				return errors.New("subscription funding already settled")
			}
		}
		if action == "refund" {
			target = 0
		}
		oldSub, oldWallet := record.PreConsumed, record.WalletPreConsumed
		oldTotal := oldSub + oldWallet
		if teamID > 0 && action == "reserve" {
			if sub.Id == 0 || sub.Status != "active" || sub.StartTime > now || sub.EndTime <= now {
				return errors.New("subscription quota insufficient: no active team subscription")
			}
			if sub.WeeklyAmount <= 0 {
				return errors.New("subscription quota insufficient: team weekly allowance not configured")
			}
			available := sub.AvailableQuota()
			if sub.WeeklyAmount > 0 {
				if record.WeeklyResetAt != sub.WeeklyResetAt {
					available = 0
				}
			}
			if (isNew && available <= 0) || target-oldTotal > available || oldWallet > 0 {
				return errors.New("subscription quota insufficient: team subscription allowance exhausted")
			}
		}
		if action == "adjust" {
			target += oldTotal
		}
		if target < 0 || target > common.MaxQuota {
			return errors.New("subscription funding total out of range")
		}
		result.AppliedDelta = target - oldTotal
		newSub := min(oldSub, target)
		if target > oldTotal {
			available := sub.AvailableQuota()
			if sub.WeeklyAmount > 0 {
				if record.WeeklyResetAt != sub.WeeklyResetAt {
					available = 0
				}
			}
			newSub = oldSub + min(target-oldTotal, available)
			if teamID > 0 && sub.Id > 0 {
				// Completed work may exceed its reservation, but any extra charge
				// remains on the original team subscription, never a wallet.
				newSub = oldSub + target - oldTotal
			}
		}
		newWallet := target - newSub
		if teamID == 0 && newWallet > 0 && sub.Id > 0 && !sub.AllowWalletOverflow {
			if action == "reserve" {
				return errors.New("subscription quota insufficient: wallet overflow disabled")
			}
			// Completed upstream work remains a debt on its original subscription
			// when the purchased contract forbids taking permanent cash.
			newSub, newWallet = target, 0
		}
		walletDelta := newWallet - oldWallet
		result.WalletDelta = walletDelta
		result.SubscriptionDelta = newSub - oldSub
		if action == "reserve" && walletDelta > 0 && walletDelta > wallet {
			return errors.New("subscription quota insufficient: combined subscription and wallet balance")
		}
		if wallet-walletDelta > common.MaxWalletQuota || wallet-walletDelta < -common.MaxWalletQuota {
			return errors.New("wallet quota out of range")
		}
		if walletDelta != 0 {
			accountQuery := tx.Model(&user)
			if teamID > 0 {
				accountQuery = tx.Model(&account)
			}
			if err := accountQuery.Update("quota", gorm.Expr("quota - ?", walletDelta)).Error; err != nil {
				return err
			}
		}
		subDelta := newSub - oldSub
		if subDelta > 0 && (sub.AmountUsed > common.MaxWalletQuota-subDelta || (sub.WeeklyAmount > 0 && record.WeeklyResetAt == sub.WeeklyResetAt && sub.WeeklyUsed > common.MaxWalletQuota-subDelta)) {
			return errors.New("subscription usage out of range")
		}
		if err := updateWorkspaceMemberWeeklyUsageTx(tx, record, result.AppliedDelta, action, sub.WeeklyResetAt); err != nil {
			return err
		}
		sub.AmountUsed = max(int64(0), sub.AmountUsed+subDelta)
		if sub.WeeklyAmount > 0 && record.WeeklyResetAt == sub.WeeklyResetAt {
			sub.WeeklyUsed = max(int64(0), sub.WeeklyUsed+subDelta)
		}
		if sub.Id > 0 {
			if err := tx.Save(sub).Error; err != nil {
				return err
			}
		}
		record.PreConsumed, record.WalletPreConsumed = newSub, newWallet
		if action == "settle" || action == "adjust" || action == "reconcile" {
			record.Status = "settled"
		}
		if action == "refund" || (action == "reconcile" && target == 0) {
			record.Status = "refunded"
		}
		if isNew {
			return tx.Create(record).Error
		}
		return tx.Save(record).Error
	})
	return result, err
}

// SubscriptionFirstFundingRecord identifies durable mixed funding after the
// in-memory session has gone away (for example asynchronous task completion).
func SubscriptionFirstFundingRecord(requestID string) (*SubscriptionPreConsumeRecord, error) {
	var record SubscriptionPreConsumeRecord
	err := DB.Where("request_id = ? AND funding_mode = ?", requestID, "subscription_first").First(&record).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	return &record, err
}
