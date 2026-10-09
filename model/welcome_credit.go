package model

import (
	"errors"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// One durable welcome campaign. Changing policy never resets who has claimed.
type WelcomePolicy struct {
	ID              int    `json:"id" gorm:"primaryKey"`
	Enabled         bool   `json:"enabled"`
	AmountCents     int64  `json:"amount_cents"`
	DailyLimitCents int64  `json:"daily_limit_cents"`
	TotalLimitCents int64  `json:"total_limit_cents"`
	IPDailyLimit    int    `json:"ip_daily_limit"`
	Day             string `json:"day" gorm:"size:10"`
	DailySpentCents int64  `json:"daily_spent_cents"`
	TotalSpentCents int64  `json:"total_spent_cents"`
}

type WelcomeCredit struct {
	UserID      int    `json:"user_id" gorm:"primaryKey;autoIncrement:false"`
	Source      string `json:"source" gorm:"size:24"`
	Status      string `json:"status" gorm:"size:24;index"`
	Reason      string `json:"reason" gorm:"size:200"`
	AmountCents int64  `json:"amount_cents"`
	Quota       int    `json:"quota"`
	IPHash      string `json:"-" gorm:"size:64;index"`
	CreatedAt   int64  `json:"created_at"`
	GrantedAt   int64  `json:"granted_at" gorm:"index"`
	ActorID     int    `json:"actor_id"`
}

// No credentials or raw IPs are retained. Multiple browsers may belong to the
// same user, but each browser can claim for only one user for this campaign.
type WelcomeBrowser struct {
	Hash      string `json:"-" gorm:"primaryKey;size:64"`
	UserID    int    `json:"user_id" gorm:"index"`
	CreatedAt int64  `json:"created_at"`
}

func InitializeWelcomePolicy() error {
	return DB.Clauses(clause.OnConflict{DoNothing: true}).Create(&WelcomePolicy{ID: 1, AmountCents: 500, DailyLimitCents: 10000, TotalLimitCents: 100000, IPDailyLimit: 3}).Error
}

func GetWelcomePolicy() (*WelcomePolicy, error) {
	var policy WelcomePolicy
	err := DB.First(&policy, 1).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return &WelcomePolicy{}, nil
	}
	return &policy, err
}

func UpdateWelcomePolicy(policy WelcomePolicy) error {
	if policy.AmountCents < 1 || policy.AmountCents > 10000 || policy.DailyLimitCents < 0 || policy.DailyLimitCents > 1000000 || policy.TotalLimitCents < 0 || policy.TotalLimitCents > 10000000 || policy.IPDailyLimit < 1 || policy.IPDailyLimit > 100 {
		return errors.New("invalid welcome policy")
	}
	return DB.Model(&WelcomePolicy{}).Where("id = ?", 1).Updates(map[string]any{"enabled": policy.Enabled, "amount_cents": policy.AmountCents, "daily_limit_cents": policy.DailyLimitCents, "total_limit_cents": policy.TotalLimitCents, "ip_daily_limit": policy.IPDailyLimit}).Error
}

func createWelcomeCandidate(tx *gorm.DB, user *User) error {
	if !user.WelcomeEligible {
		return nil
	}
	return tx.Create(&WelcomeCredit{UserID: user.Id, Source: "registration", Status: "pending", CreatedAt: common.GetTimestamp()}).Error
}

func GetWelcomeCredit(userID int) (*WelcomeCredit, error) {
	var credit WelcomeCredit
	err := DB.First(&credit, "user_id = ?", userID).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return &WelcomeCredit{UserID: userID, Status: "ineligible"}, nil
	}
	return &credit, err
}

// Freeze an explicit one-time cohort, never a recurring "balance == 0" rule.
func EnrollWelcomeBackfill(userIDs []int) error {
	if len(userIDs) == 0 || len(userIDs) > 500 {
		return errors.New("invalid cohort")
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		for _, id := range userIDs {
			var user User
			if err := lockForUpdate(tx).Where("id = ? AND quota = ? AND status = ? AND role = ?", id, 0, common.UserStatusEnabled, common.RoleCommonUser).First(&user).Error; err != nil {
				return err
			}
			if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&WelcomeCredit{UserID: id, Source: "backfill", Status: "pending", CreatedAt: common.GetTimestamp()}).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

// Campaign lock serializes browser/IP/budget claims on MySQL and PostgreSQL;
// SQLite uses the gateway's immediate write transaction. All ledger changes
// commit together. No cache or asynchronous log is the source of truth.
func GrantWelcomeCredit(userID int, browserHash, ipHash string, actorID int, manualReason string) (*WelcomeCredit, error) {
	manual := actorID > 0
	if manual && len(strings.TrimSpace(manualReason)) < 3 {
		return nil, errors.New("review reason required")
	}
	var result WelcomeCredit
	granted := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var policy WelcomePolicy
		if err := lockForUpdate(tx).First(&policy, 1).Error; err != nil {
			return err
		}
		// Retain the immutable monetary ledger, but expire network identifiers.
		if err := tx.Model(&WelcomeCredit{}).Where("granted_at > ? AND granted_at < ? AND ip_hash <> ?", 0, common.GetTimestamp()-30*86400, "").UpdateColumn("ip_hash", "").Error; err != nil {
			return err
		}
		if err := lockForUpdate(tx).First(&result, "user_id = ?", userID).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				result = WelcomeCredit{UserID: userID, Status: "ineligible"}
				return nil
			}
			return err
		}
		if result.Status == "granted" {
			if browserHash != "" {
				return tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&WelcomeBrowser{Hash: browserHash, UserID: userID, CreatedAt: common.GetTimestamp()}).Error
			}
			return nil
		}
		if result.Status == "skipped" {
			return nil
		}
		var user User
		if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
			return err
		}
		reason := ""
		if user.Status != common.UserStatusEnabled || user.Role != common.RoleCommonUser {
			reason = "account_unavailable"
		}
		if result.Source == "backfill" && user.Quota != 0 {
			result.Status = "skipped"
			reason = "balance_changed"
		}
		if !manual && !policy.Enabled {
			reason = "paused"
		}
		if !manual && result.Source == "backfill" {
			reason = "awaiting_backfill"
		}
		if !manual && (browserHash == "" || ipHash == "") {
			reason = "browser_required"
		}
		now := common.GetTimestamp()
		day := time.Unix(now, 0).In(time.FixedZone("Asia/Shanghai", 8*3600)).Format("2006-01-02")
		if policy.Day != day {
			policy.Day = day
			policy.DailySpentCents = 0
		}
		if policy.TotalSpentCents+policy.AmountCents > policy.TotalLimitCents || (result.Source == "registration" && policy.DailySpentCents+policy.AmountCents > policy.DailyLimitCents) {
			reason = "budget_reached"
		}
		if !manual && reason == "" {
			var browser WelcomeBrowser
			err := tx.First(&browser, "hash = ?", browserHash).Error
			if err == nil && browser.UserID != userID {
				reason = "browser_used"
			} else if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
				return err
			}
			var count int64
			if err := tx.Model(&WelcomeCredit{}).Where("ip_hash = ? AND status = ? AND granted_at > ?", ipHash, "granted", now-86400).Count(&count).Error; err != nil {
				return err
			}
			if count >= int64(policy.IPDailyLimit) {
				reason = "ip_limit"
			}
		}
		if reason != "" {
			result.Reason = reason
			return tx.Save(&result).Error
		}
		quota, err := CNYCentsToQuota(policy.AmountCents)
		if err != nil {
			return err
		}
		if user.Quota > common.MaxWalletQuota-quota {
			return errors.New("wallet limit")
		}
		if !manual {
			if err := tx.Create(&WelcomeBrowser{Hash: browserHash, UserID: userID, CreatedAt: now}).Error; err != nil {
				return err
			}
		}
		if err := tx.Model(&user).UpdateColumn("quota", gorm.Expr("quota + ?", quota)).Error; err != nil {
			return err
		}
		policy.TotalSpentCents += policy.AmountCents
		if result.Source == "registration" {
			policy.DailySpentCents += policy.AmountCents
		}
		if err := tx.Save(&policy).Error; err != nil {
			return err
		}
		result.Status = "granted"
		result.AmountCents = policy.AmountCents
		result.Quota = quota
		result.GrantedAt = now
		result.IPHash = ipHash
		result.ActorID = actorID
		result.Reason = manualReason
		granted = true
		return tx.Save(&result).Error
	})
	if err == nil && granted {
		syncCreditUserQuotaCache(userID, result.Quota, "welcome")
		RecordLog(userID, LogTypeSystem, "欢迎体验额度已到账")
	}
	return &result, err
}

// Total historical wallet consumption plus remaining funds distinguishes a
// gift-only account from one that has received additional wallet funds.
func IsWelcomeTrial(userID int) (bool, error) {
	credit, err := GetWelcomeCredit(userID)
	if err != nil || credit.Status != "granted" {
		return false, err
	}
	var user User
	if err := DB.Select("id", "quota", "used_quota", "role").First(&user, userID).Error; err != nil {
		return false, err
	}
	if user.Role != common.RoleCommonUser || int64(user.Quota)+int64(user.UsedQuota) > int64(credit.Quota) {
		return false, nil
	}
	var count int64
	err = DB.Model(&UserSubscription{}).Where("user_id = ? AND status = ? AND end_time > ?", userID, "active", common.GetTimestamp()).Count(&count).Error
	return count == 0, err
}
