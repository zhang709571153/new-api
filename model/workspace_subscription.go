package model

import (
	"errors"
	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
	"gorm.io/gorm"
)

// SetWorkspaceSubscription reuses upstream subscription funding, expiry and
// settlement. It grants service credit only; it never charges or renews payment.
func SetWorkspaceSubscription(userID int, monthly, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	return setWorkspaceSubscription(userID, 0, &monthly, weekly, context...)
}

func SetWorkspaceTeamSubscription(ownerID, teamID int, monthly, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	if teamID <= 0 {
		return nil, ErrWorkspaceAccess
	}
	return setWorkspaceSubscription(ownerID, teamID, &monthly, weekly, context...)
}

// SetWorkspaceWeeklySubscription grants four weekly windows, or scales the
// existing administrator entitlement without renewing or resetting it.
func SetWorkspaceWeeklySubscription(userID, teamID int, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	return setWorkspaceSubscription(userID, teamID, nil, weekly, context...)
}

// WorkspaceFundingContext guards shortcuts opened from a team snapshot. The
// team lock makes the version check atomic with edits and new allowance grants.
type WorkspaceFundingContext struct {
	TeamID         int `json:"team_id"`
	FundingVersion int `json:"funding_version"`
}

func lockWorkspaceFundingContext(tx *gorm.DB, userID, teamID int, context []*WorkspaceFundingContext) error {
	if len(context) == 0 || context[0] == nil {
		return nil
	}
	expected := context[0]
	if expected.TeamID <= 0 || expected.FundingVersion < 0 || expected.FundingVersion > 1 || (expected.FundingVersion == 0 && teamID != 0) || (expected.FundingVersion == 1 && teamID != expected.TeamID) {
		return ErrWorkspaceAccess
	}
	var team WorkspaceTeam
	if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ? AND funding_version = ?", expected.TeamID, userID, expected.FundingVersion).Error; err != nil {
		return errors.New("团队信息已变化，请刷新页面后再调整订阅")
	}
	return nil
}

// UpdateWorkspaceSubscriptionLimits changes an exact entitlement, including paid
// subscriptions. It does not grant credit, renew, reset usage, or change policy.
func UpdateWorkspaceSubscriptionLimits(userID, teamID, subscriptionID int, monthly, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	return updateWorkspaceSubscriptionLimits(userID, teamID, subscriptionID, &monthly, weekly, context...)
}

func UpdateWorkspaceSubscriptionWeeklyLimit(userID, teamID, subscriptionID int, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	return updateWorkspaceSubscriptionLimits(userID, teamID, subscriptionID, nil, weekly, context...)
}

// Change only the expiry of a currently active entitlement. Compare the original
// timestamp under the funding locks so a stale form cannot overwrite an edit.
// Reservations and their original weekly windows remain available for settlement.
func UpdateWorkspaceSubscriptionExpiry(userID, teamID, subscriptionID int, endTime, expectedEndTime int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	if userID <= 0 || teamID < 0 || subscriptionID <= 0 || expectedEndTime <= 0 || endTime <= 0 || endTime > 253402300799 {
		return nil, errors.New("订阅或到期时间无效")
	}
	var result UserSubscription
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockWorkspaceFundingContext(tx, userID, teamID, context); err != nil {
			return err
		}
		if teamID > 0 {
			var team WorkspaceTeam
			if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ? AND funding_version = 1", teamID, userID).Error; err != nil {
				return ErrWorkspaceAccess
			}
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ? AND owner_user_id = ? AND closed_at = 0", teamID, userID).Error; err != nil {
				return ErrWorkspaceAccess
			}
		}
		var user User
		if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
			return err
		}
		now := getDBTimestampTx(tx)
		if endTime <= now {
			return errors.New("到期时间必须晚于当前时间；立即停用请使用结束订阅")
		}
		var active []UserSubscription
		if err := lockForUpdate(tx).Where("user_id = ? AND workspace_team_id = ? AND status = ? AND end_time > ?", userID, teamID, "active", now).Limit(2).Find(&active).Error; err != nil {
			return err
		}
		if len(active) != 1 || active[0].Id != subscriptionID || active[0].StartTime > now {
			return errors.New("订阅已变化或不属于所选账户，请刷新后重试")
		}
		result = active[0]
		if result.EndTime != expectedEndTime {
			return errors.New("到期时间已变化，请刷新后重试")
		}
		return tx.Model(&result).Update("end_time", endTime).Error
	})
	if err != nil {
		return nil, err
	}
	if err := invalidateUserCache(userID); err != nil {
		common.SysError("subscription user cache invalidation failed")
	}
	result.RefreshWeeklyWindow(common.GetTimestamp())
	return &result, nil
}

func updateWorkspaceSubscriptionLimits(userID, teamID, subscriptionID int, monthly *int64, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	if userID <= 0 || teamID < 0 || subscriptionID <= 0 || weekly <= 0 || weekly > common.MaxWalletQuota || (monthly != nil && (*monthly <= 0 || *monthly > common.MaxWalletQuota || weekly > *monthly)) {
		if monthly == nil {
			return nil, errors.New("周限额必须大于零且在支持范围内")
		}
		return nil, errors.New("月额度和周限额必须大于零，且周限额不能超过月额度")
	}
	var result UserSubscription
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockWorkspaceFundingContext(tx, userID, teamID, context); err != nil {
			return err
		}
		if teamID > 0 {
			var team WorkspaceTeam
			if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ? AND funding_version = 1", teamID, userID).Error; err != nil {
				return ErrWorkspaceAccess
			}
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ? AND owner_user_id = ? AND closed_at = 0", teamID, userID).Error; err != nil {
				return ErrWorkspaceAccess
			}
		}
		var user User
		if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
			return err
		}
		now := getDBTimestampTx(tx)
		if teamID > 0 {
			var active int64
			if err := tx.Model(&UserSubscription{}).Where("workspace_team_id = ? AND status = ? AND end_time > ?", teamID, "active", now).Count(&active).Error; err != nil {
				return err
			}
			if active > 1 {
				return ErrWorkspaceTeamSubscriptionConflict
			}
		} else {
			var active int64
			if err := tx.Model(&UserSubscription{}).Where("workspace_team_id = 0 AND user_id = ? AND status = ? AND end_time > ?", userID, "active", now).Count(&active).Error; err != nil {
				return err
			}
			if active > 1 {
				return ErrSubscriptionConflict
			}
		}
		if err := lockForUpdate(tx).Where("id = ? AND user_id = ? AND workspace_team_id = ? AND status = ? AND start_time <= ? AND end_time > ?", subscriptionID, userID, teamID, "active", now, now).First(&result).Error; err != nil {
			return errors.New("订阅已失效或不属于所选账户，请刷新后重试")
		}
		total, err := workspaceSubscriptionTotal(&result, monthly, weekly)
		if err != nil {
			return err
		}
		// Only these columns may change. In-flight reservations and settlement,
		// purchase metadata, reset schedule and wallet policy stay intact.
		if err := tx.Model(&result).Updates(map[string]any{"amount_total": total, "weekly_amount": weekly}).Error; err != nil {
			return err
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if err := invalidateUserCache(userID); err != nil {
		common.SysError("subscription user cache invalidation failed")
	}
	// Refresh only the response snapshot, just like subscription reads do.
	result.RefreshWeeklyWindow(common.GetTimestamp())
	return &result, nil
}

// The original total/weekly ratio belongs to the purchased entitlement. Scaling
// under its row lock preserves that ratio without changing dates or usage.
func workspaceSubscriptionTotal(existing *UserSubscription, monthly *int64, weekly int64) (int64, error) {
	if monthly != nil {
		return *monthly, nil
	}
	total := decimal.NewFromInt(weekly).Mul(decimal.NewFromInt(4))
	if existing != nil {
		total = decimal.NewFromInt(existing.AmountTotal)
		if existing.WeeklyAmount > 0 {
			total = total.Mul(decimal.NewFromInt(weekly)).DivRound(decimal.NewFromInt(existing.WeeklyAmount), 0)
		}
	}
	quota, err := common.WalletQuotaFromDecimalStrict(total)
	if err != nil || quota <= 0 || int64(quota) < weekly {
		return 0, errors.New("周限额超出订阅支持范围")
	}
	return int64(quota), nil
}

// An explicit weekly correction is independent of lifetime consumption and of
// every member's weekly counter. Pending reservations must finish first.
func UpdateWorkspaceTeamSubscriptionWeeklyUsed(userID, teamID, subscriptionID int, used, expectedResetAt int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	if userID <= 0 || teamID <= 0 || subscriptionID <= 0 || used < 0 || used > common.MaxWalletQuota || expectedResetAt <= 0 {
		return nil, errors.New("本周已用额度超出范围")
	}
	var result UserSubscription
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockWorkspaceFundingContext(tx, userID, teamID, context); err != nil {
			return err
		}
		var team WorkspaceTeam
		if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ? AND funding_version = 1", teamID, userID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		var account WorkspaceTeamAccount
		if err := lockForUpdate(tx).First(&account, "team_id = ? AND owner_user_id = ? AND closed_at = 0", teamID, userID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		now := getDBTimestampTx(tx)
		var active []UserSubscription
		if err := lockForUpdate(tx).Where("workspace_team_id = ? AND status = ? AND end_time > ?", teamID, "active", now).Limit(2).Find(&active).Error; err != nil {
			return err
		}
		if len(active) > 1 {
			return ErrWorkspaceTeamSubscriptionConflict
		}
		if len(active) != 1 || active[0].Id != subscriptionID || active[0].UserId != userID || active[0].StartTime > now || active[0].WeeklyAmount <= 0 {
			return errors.New("订阅已失效或不属于所选账户，请刷新后重试")
		}
		result = active[0]
		result.RefreshWeeklyWindow(now)
		if result.WeeklyResetAt != expectedResetAt {
			return errors.New("周额度周期已变化，请刷新页面后重试")
		}
		var pending []SubscriptionPreConsumeRecord
		if err := lockForUpdate(tx).Where("user_subscription_id = ? AND status = ?", subscriptionID, "consumed").Limit(1).Find(&pending).Error; err != nil {
			return err
		}
		if len(pending) != 0 {
			return errors.New("仍有请求结算中，请稍后再试")
		}
		result.WeeklyUsed = used
		return tx.Model(&result).Updates(map[string]any{"weekly_used": used, "weekly_reset_at": result.WeeklyResetAt}).Error
	})
	return &result, err
}

// SetWorkspaceWeeklySubscriptionTx is used by explicit administrator provisioning.
// It shares the caller's team/user transaction and performs no cache mutation.
func SetWorkspaceWeeklySubscriptionTx(tx *gorm.DB, userID, teamID int, weekly int64) (*UserSubscription, error) {
	if tx == nil {
		return nil, errors.New("tx is nil")
	}
	return setWorkspaceSubscriptionTx(tx, userID, teamID, nil, weekly)
}

func setWorkspaceSubscription(userID, teamID int, monthly *int64, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	var result *UserSubscription
	err := DB.Transaction(func(tx *gorm.DB) error {
		var err error
		result, err = setWorkspaceSubscriptionTx(tx, userID, teamID, monthly, weekly, context...)
		return err
	})
	if err != nil {
		return nil, err
	}
	if err := invalidateUserCache(userID); err != nil {
		common.SysError("subscription user cache invalidation failed")
	}
	return result, nil
}

func setWorkspaceSubscriptionTx(tx *gorm.DB, userID, teamID int, monthly *int64, weekly int64, context ...*WorkspaceFundingContext) (*UserSubscription, error) {
	if userID <= 0 || teamID < 0 || weekly <= 0 || weekly > common.MaxWalletQuota || (monthly != nil && (*monthly <= 0 || *monthly > common.MaxWalletQuota || weekly > *monthly)) {
		if monthly == nil {
			return nil, errors.New("周限额必须大于零且在支持范围内")
		}
		return nil, errors.New("月额度和周限额必须大于零，且周限额不能超过月额度")
	}
	var result UserSubscription
	err := func() error {
		if err := lockWorkspaceFundingContext(tx, userID, teamID, context); err != nil {
			return err
		}
		var user User
		if teamID > 0 {
			var team WorkspaceTeam
			if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ? AND funding_version = 1", teamID, userID).Error; err != nil {
				return ErrWorkspaceAccess
			}
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ? AND owner_user_id = ? AND closed_at = 0", teamID, userID).Error; err != nil {
				return ErrWorkspaceAccess
			}
		}
		if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
			return err
		}
		now := getDBTimestampTx(tx)
		var active []UserSubscription
		query := lockForUpdate(tx).Where("workspace_team_id = ? AND status = ? AND end_time > ?", teamID, "active", now)
		if teamID == 0 {
			query = query.Where("user_id = ?", userID)
		}
		if err := query.Find(&active).Error; err != nil {
			return err
		}
		if teamID > 0 && len(active) > 1 {
			return ErrWorkspaceTeamSubscriptionConflict
		}
		if len(active) > 1 {
			return ErrSubscriptionConflict
		}
		if len(active) > 0 {
			if teamID > 0 && active[0].UserId != userID {
				return ErrWorkspaceAccess
			}
			if len(active) != 1 || active[0].Source != "workspace_admin" {
				return errors.New("已有其他订阅，请先在订阅管理中处理")
			}
			result = active[0]
			total, err := workspaceSubscriptionTotal(&result, monthly, weekly)
			if err != nil {
				return err
			}
			result.AmountTotal, result.WeeklyAmount = total, weekly
			result.AllowWalletOverflow = teamID == 0
			// Preserve consumed amounts, dates and pending request reservations.
			if err := tx.Save(&result).Error; err != nil {
				return err
			}
		} else {
			total, err := workspaceSubscriptionTotal(nil, monthly, weekly)
			if err != nil {
				return err
			}
			var plan SubscriptionPlan
			if err := tx.Where("title = ? AND price_amount = ?", "Realyu 月度额度（管理员开通）", 0).Limit(1).Find(&plan).Error; err != nil {
				return err
			}
			if plan.Id == 0 {
				plan = SubscriptionPlan{Title: "Realyu 月度额度（管理员开通）", Currency: "USD", DurationUnit: SubscriptionDurationMonth, DurationValue: 1, QuotaResetPeriod: SubscriptionResetNever, AllowBalancePay: common.GetPointer(false), AllowWalletOverflow: common.GetPointer(true)}
				if err := tx.Create(&plan).Error; err != nil {
					return err
				}
				if err := tx.Model(&plan).Update("enabled", false).Error; err != nil {
					return err
				}
			}
			created, err := createScopedSubscriptionFromPlanTx(tx, userID, teamID, &plan, "workspace_admin")
			if err != nil {
				return err
			}
			result = *created
			result.WorkspaceTeamID = teamID
			result.AmountTotal, result.WeeklyAmount = total, weekly
			result.AllowWalletOverflow = teamID == 0
			if monthly == nil {
				result.EndTime = result.StartTime + BillingPeriodSeconds
			}
			result.WeeklyResetAt = result.StartTime + 7*24*3600
			if err := tx.Save(&result).Error; err != nil {
				return err
			}
		}
		if teamID > 0 {
			return nil
		}
		setting := user.GetSetting()
		setting.BillingPreference = "subscription_first"
		encoded, err := common.Marshal(setting)
		if err != nil {
			return err
		}
		return tx.Model(&user).Update("setting", string(encoded)).Error
	}()
	if err != nil {
		return nil, err
	}
	result.RefreshWeeklyWindow(common.GetTimestamp())
	return &result, nil
}

// EnableWorkspaceSubscriptionWalletOverflow applies the unified funding policy
// to existing administrator grants without changing budgets, usage or dates.
func EnableWorkspaceSubscriptionWalletOverflow() error {
	return DB.Model(&UserSubscription{}).
		Where("workspace_team_id = 0 AND source = ? AND (allow_wallet_overflow = ? OR allow_wallet_overflow IS NULL)", "workspace_admin", false).
		Update("allow_wallet_overflow", true).Error
}

// WorkspaceFunding reports the shared funding owner, never another team's data.
func WorkspaceFunding(userID int) (float64, []SubscriptionSummary, error) {
	account, err := GetWorkspaceAccountSnapshot(userID)
	if err != nil {
		return 0, nil, err
	}
	subs, err := GetAllActiveUserSubscriptions(userID)
	if err != nil {
		return 0, nil, err
	}
	if len(subs) > 1 {
		return 0, subs, nil
	}
	available := float64(max(0, account.Quota)) / common.QuotaPerUnit
	for _, item := range subs {
		if !item.Subscription.AllowWalletOverflow {
			available = 0
			break
		}
	}
	for _, item := range subs {
		s := item.Subscription
		remaining := s.AvailableQuota()
		available += float64(remaining) / common.QuotaPerUnit
	}
	return available, subs, nil
}
