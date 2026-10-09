package model

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
	"gorm.io/gorm"
)

// BillingOrder is the immutable checkout quote and fulfillment ledger. Cents
// and quota are snapshotted before payment; provider callbacks never supply rights.
type BillingOrder struct {
	FundingScope      string `json:"funding_scope" gorm:"type:varchar(16);not null;default:'personal'"`
	WorkspaceTeamID   int    `json:"workspace_team_id" gorm:"not null;default:0"`
	WeeklyAmount      int64  `json:"weekly_amount" gorm:"-"`
	AllowBalancePay   bool   `json:"allow_balance_pay" gorm:"-"`
	Id                int    `json:"id"`
	TradeNo           string `json:"trade_no" gorm:"type:varchar(64);uniqueIndex"`
	UserId            int    `json:"user_id" gorm:"index"`
	Kind              string `json:"kind" gorm:"type:varchar(24)"`
	PlanId            int    `json:"plan_id"`
	PlanSnapshot      string `json:"-" gorm:"type:text"`
	PlanTitle         string `json:"plan_title" gorm:"type:varchar(128)"`
	PriceCents        int64  `json:"price_cents" gorm:"type:bigint"`
	CreditedQuota     int64  `json:"credited_quota" gorm:"type:bigint"`
	OldSubscriptionId int    `json:"old_subscription_id"`
	RemainingWeeks    int64  `json:"remaining_weeks" gorm:"type:bigint"`
	ResidualCents     int64  `json:"residual_cents" gorm:"type:bigint"`
	ExtensionSeconds  int64  `json:"extension_seconds" gorm:"type:bigint"`
	DurationSeconds   int64  `json:"duration_seconds" gorm:"type:bigint"`
	Status            string `json:"status" gorm:"type:varchar(24);index"`
	PaymentMethod     string `json:"payment_method" gorm:"type:varchar(32)"`
	CreatedAt         int64  `json:"created_at" gorm:"type:bigint"`
	ExpiresAt         int64  `json:"expires_at" gorm:"type:bigint"`
	CompletedAt       int64  `json:"completed_at" gorm:"type:bigint"`
	SubscriptionId    int    `json:"subscription_id"`
	// Fingerprint prevents a retry key from silently referring to another purchase.
	RequestKey         string `json:"-" gorm:"type:varchar(64);uniqueIndex"`
	RequestFingerprint string `json:"-" gorm:"type:varchar(64)"`
}

func (o *BillingOrder) AfterFind(_ *gorm.DB) error {
	if o.PlanSnapshot == "" {
		return nil
	}
	var plan SubscriptionPlan
	if err := common.UnmarshalJsonStr(o.PlanSnapshot, &plan); err != nil {
		return err
	}
	o.AllowBalancePay = o.Kind == "convert" || plan.AllowBalancePay == nil || *plan.AllowBalancePay
	o.WeeklyAmount = plan.WeeklyAmount
	return nil
}

type BillingCheckoutRequest struct {
	FundingScope      string  `json:"funding_scope"`
	Kind              string  `json:"kind"` // paygo, subscription, upgrade, convert
	PlanId            int     `json:"plan_id"`
	AmountCNY         float64 `json:"amount_cny"`
	OldSubscriptionId int     `json:"old_subscription_id"`
	IdempotencyKey    string  `json:"idempotency_key"`
}

func billingHash(text string) string {
	value := sha256.Sum256([]byte(text))
	return hex.EncodeToString(value[:])
}

// CNY subscriptions settle the subscription and wallet ledgers in one database
// transaction. The legacy Redis/batch wallet path has a different authority and
// must not run alongside that transaction until the two protocols are unified.
func EnsureCNYBillingLedgerMode() error {
	if common.RedisEnabled || common.BatchUpdateEnabled {
		return errors.New("CNY subscription billing requires Redis and batch updates to be disabled")
	}
	return nil
}

func lockBillingUser(tx *gorm.DB, userId int) (*User, error) {
	// Team lifecycle locks team before user. Match that order, then recheck the
	// membership snapshot so a concurrent join never changes checkout identity.
	var membership WorkspaceMember
	if err := tx.Where("user_id = ?", userId).Limit(1).Find(&membership).Error; err != nil {
		return nil, err
	}
	var team WorkspaceTeam
	if membership.TeamID > 0 {
		if err := lockForUpdate(tx).First(&team, membership.TeamID).Error; err != nil {
			return nil, err
		}
		if team.FundingVersion > 0 {
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ?", team.ID).Error; err != nil {
				return nil, err
			}
		}
	}
	var user User
	if err := lockForUpdate(tx).First(&user, userId).Error; err != nil {
		return nil, err
	}
	var current WorkspaceMember
	if err := lockForUpdate(tx).Where("user_id = ?", userId).Limit(1).Find(&current).Error; err != nil {
		return nil, err
	}
	if current.TeamID != membership.TeamID || current.TokenID != membership.TokenID {
		return nil, errors.New("team membership changed; retry checkout")
	}
	if user.Status != common.UserStatusEnabled {
		return &user, errors.New("account disabled")
	}
	// Checkout belongs to this personal account even when it has a team.
	return &user, nil
}

func CreateBillingCheckout(userId int, req BillingCheckoutRequest) (*BillingOrder, error) {
	if userId <= 0 || len(req.IdempotencyKey) < 16 || len(req.IdempotencyKey) > 128 {
		return nil, errors.New("invalid checkout identity")
	}
	requestKey := billingHash(fmt.Sprintf("%d:%s", userId, req.IdempotencyKey))
	req.FundingScope = NormalizeFundingScope(req.FundingScope)
	if req.FundingScope != "personal" && req.FundingScope != "team" {
		return nil, errors.New("invalid funding scope")
	}
	// Preserve existing whole-yuan fingerprints while accepting exact cents.
	fingerprint := billingHash(fmt.Sprintf("%s:%d:%s:%d", req.Kind, req.PlanId, strconv.FormatFloat(req.AmountCNY, 'f', -1, 64), req.OldSubscriptionId))
	if req.FundingScope == "team" {
		fingerprint = billingHash("team:" + fingerprint)
	}
	var order BillingOrder
	err := DB.Transaction(func(tx *gorm.DB) error {
		if _, err := lockBillingUser(tx, userId); err != nil {
			return err
		}
		err := tx.Where("request_key = ?", requestKey).First(&order).Error
		if err == nil {
			if order.RequestFingerprint != fingerprint {
				return errors.New("checkout key already used for a different request")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		now := getDBTimestampTx(tx)
		order = BillingOrder{UserId: userId, Kind: req.Kind, PlanId: req.PlanId, OldSubscriptionId: req.OldSubscriptionId, TradeNo: "RY" + common.GetRandomString(30), RequestKey: requestKey, RequestFingerprint: fingerprint, Status: "pending", CreatedAt: now, ExpiresAt: now + 900}
		order.FundingScope = req.FundingScope
		if req.Kind == "paygo" {
			if req.FundingScope != "personal" {
				return errors.New("teams do not support PAYGO")
			}
			if req.PlanId != 0 || req.OldSubscriptionId != 0 {
				return errors.New("PAYGO cannot include a subscription")
			}
			price, err := BillingPriceCents(req.AmountCNY)
			if err != nil {
				return err
			}
			order.PriceCents = price
			quota, err := CNYCentsToQuota(order.PriceCents)
			if err != nil {
				return err
			}
			order.CreditedQuota = int64(quota)
			return tx.Create(&order).Error
		}
		if req.Kind != "subscription" && req.Kind != "upgrade" && req.Kind != "convert" {
			return errors.New("invalid checkout kind")
		}
		if err := EnsureCNYBillingLedgerMode(); err != nil {
			return err
		}
		var plan SubscriptionPlan
		if err := tx.First(&plan, req.PlanId).Error; err != nil {
			return err
		}
		if !plan.Enabled || !plan.IsCNYWeekly() {
			return errors.New("plan unavailable")
		}
		if err := ValidateCNYPlan(&plan); err != nil {
			return err
		}
		if plan.FundingScope != req.FundingScope {
			return errors.New("plan does not belong to selected funding scope")
		}
		price, err := BillingPriceCents(plan.PriceAmount)
		if err != nil {
			return err
		}
		active, err := billingSubscriptionsForScope(tx, &order, now, true)
		if err != nil {
			return err
		}
		if req.Kind == "subscription" {
			if len(active) != 0 || req.OldSubscriptionId != 0 {
				return errors.New("an active subscription already exists; use upgrade or wait until expiry")
			}
		} else {
			if len(active) != 1 || active[0].Id != req.OldSubscriptionId {
				return errors.New("subscription changed; refresh the upgrade quote")
			}
			value, err := CalculateBillingUpgrade(&active[0], price, now)
			if err != nil {
				return err
			}
			order.RemainingWeeks, order.ResidualCents, order.ExtensionSeconds = value.RemainingWeeks, value.ResidualCents, value.ExtensionSeconds
			if req.Kind == "convert" && value.ExtensionSeconds == 0 {
				return errors.New("no complete future weeks remain")
			}
		}
		order.PriceCents, order.PlanTitle = price, plan.Title
		order.DurationSeconds = BillingPeriodSeconds + order.ExtensionSeconds
		if req.Kind == "convert" {
			order.PriceCents = 0
			order.DurationSeconds = order.ExtensionSeconds
		}
		quota, err := common.WalletQuotaFromDecimalStrict(decimal.NewFromInt(plan.WeeklyAmount).Mul(decimal.NewFromInt(order.DurationSeconds)).Div(decimal.NewFromInt(BillingWeekSeconds)).Floor())
		if err != nil {
			return err
		}
		order.CreditedQuota = int64(quota)
		data, err := common.Marshal(plan)
		if err != nil {
			return err
		}
		order.PlanSnapshot = string(data)
		order.WeeklyAmount = plan.WeeklyAmount
		order.AllowBalancePay = req.Kind == "convert" || plan.AllowBalancePay == nil || *plan.AllowBalancePay
		return tx.Create(&order).Error
	})
	return &order, err
}

func GetBillingOrder(userId int, tradeNo string) (*BillingOrder, error) {
	var order BillingOrder
	err := DB.Where("user_id = ? AND trade_no = ?", userId, tradeNo).First(&order).Error
	return &order, err
}

func GetBillingOrderForPayment(tradeNo string) (*BillingOrder, error) {
	var order BillingOrder
	err := DB.Where("trade_no = ?", tradeNo).First(&order).Error
	return &order, err
}

// Claim the payment rail under the same user lock as fulfillment. An order
// cannot be both charged from the wallet and sent to an external gateway.
func ClaimBillingEpay(userId int, tradeNo, method string) (*BillingOrder, error) {
	var order BillingOrder
	err := DB.Transaction(func(tx *gorm.DB) error {
		if _, err := lockBillingUser(tx, userId); err != nil {
			return err
		}
		if err := lockForUpdate(tx).Where("user_id = ? AND trade_no = ?", userId, tradeNo).First(&order).Error; err != nil {
			return err
		}
		if order.Status != "pending" || order.ExpiresAt <= getDBTimestampTx(tx) || order.PriceCents <= 0 {
			return errors.New("checkout expired or completed")
		}
		if order.Kind != "paygo" {
			if err := EnsureCNYBillingLedgerMode(); err != nil {
				return err
			}
		}
		if order.PaymentMethod != "" && order.PaymentMethod != method {
			return errors.New("payment method already selected")
		}
		order.PaymentMethod = method
		return tx.Save(&order).Error
	})
	return &order, err
}

// The caller holds the user's lifecycle lock. Team ownership is checked again
// at fulfillment: quoting is never enough to grant an owner role.
func billingSubscriptionsForScope(tx *gorm.DB, order *BillingOrder, now int64, quoting bool) ([]UserSubscription, error) {
	teamID := 0
	if NormalizeFundingScope(order.FundingScope) == "team" {
		var team WorkspaceTeam
		if err := lockForUpdate(tx).Where("owner_user_id = ?", order.UserId).Limit(1).Find(&team).Error; err != nil {
			return nil, err
		}
		if order.Kind == "subscription" {
			if team.ID != 0 || order.WorkspaceTeamID != 0 {
				return nil, errors.New("team owners can only upgrade their current team subscription")
			}
			return nil, nil
		}
		if team.ID == 0 || team.FundingVersion != 1 || (!quoting && order.WorkspaceTeamID != team.ID) {
			return nil, errors.New("owned team changed; refresh the upgrade quote")
		}
		teamID = team.ID
		if quoting {
			order.WorkspaceTeamID = teamID
		}
		var account WorkspaceTeamAccount
		if err := lockForUpdate(tx).First(&account, "team_id = ? AND owner_user_id = ? AND closed_at = 0", teamID, order.UserId).Error; err != nil {
			return nil, ErrWorkspaceAccess
		}
	}
	var active []UserSubscription
	query := lockForUpdate(tx).Where("workspace_team_id = ? AND status = ? AND end_time > ?", teamID, "active", now)
	if teamID == 0 {
		query = query.Where("user_id = ?", order.UserId)
	}
	if err := query.Find(&active).Error; err != nil {
		return nil, err
	}
	for _, sub := range active {
		if sub.UserId != order.UserId {
			return nil, ErrWorkspaceAccess
		}
	}
	return active, nil
}

func billingEligibility(tx *gorm.DB, order *BillingOrder, now int64) error {
	active, err := billingSubscriptionsForScope(tx, order, now, false)
	if err != nil {
		return err
	}
	if order.OldSubscriptionId == 0 {
		if len(active) != 0 {
			return errors.New("an active subscription already exists")
		}
		return nil
	}
	if len(active) != 1 || active[0].Id != order.OldSubscriptionId {
		return errors.New("subscription changed; request a new quote")
	}
	var plan SubscriptionPlan
	if err := common.UnmarshalJsonStr(order.PlanSnapshot, &plan); err != nil {
		return err
	}
	price, err := BillingPriceCents(plan.PriceAmount)
	if err != nil {
		return err
	}
	value, err := CalculateBillingUpgrade(&active[0], price, now)
	if err != nil {
		return err
	}
	if value.RemainingWeeks != order.RemainingWeeks || value.ResidualCents != order.ResidualCents {
		return errors.New("weekly boundary crossed; request a new quote")
	}
	return nil
}

// External payments that arrive after an upgrade quote expires or its source
// changes become permanent wallet credit, never lost money or duplicate rights.
func FulfillBillingOrder(userId int, tradeNo, method string, paidCents int64, external bool) (*BillingOrder, error) {
	var result BillingOrder
	var committedQuotaDelta int64
	var committedSetting string
	var revokedKeys []string
	err := DB.Transaction(func(tx *gorm.DB) error {
		var user *User
		var eligibilityErr error
		if external {
			user, eligibilityErr = lockBillingUser(tx, userId)
			if user == nil {
				return eligibilityErr
			}
		} else {
			var err error
			user, err = lockBillingUser(tx, userId)
			if err != nil {
				return err
			}
		}
		if err := lockForUpdate(tx).Where("user_id = ? AND trade_no = ?", userId, tradeNo).First(&result).Error; err != nil {
			return err
		}
		if result.Status == "paid" || result.Status == "wallet_credited" {
			if external && (result.PaymentMethod != method || result.PriceCents != paidCents) {
				return errors.New("payment mismatch")
			}
			return nil
		}
		if result.Status != "pending" {
			return errors.New("invalid order state")
		}
		now := getDBTimestampTx(tx)
		if external {
			if result.PaymentMethod != method || method == "balance" || result.PriceCents != paidCents || paidCents <= 0 {
				return errors.New("payment mismatch")
			}
		} else {
			if method != "balance" || result.PaymentMethod != "" || result.Kind == "paygo" || result.ExpiresAt <= now {
				return errors.New("checkout expired or payment method unavailable")
			}
		}
		eligible := eligibilityErr
		if result.Kind != "paygo" && eligible == nil {
			eligible = EnsureCNYBillingLedgerMode()
		}
		if result.Kind != "paygo" && eligible == nil {
			eligible = billingEligibility(tx, &result, now)
		}
		if !external && eligible != nil {
			return eligible
		}
		walletCredit := result.Kind == "paygo" || (external && (eligible != nil || result.ExpiresAt <= now))
		if walletCredit {
			quota, err := CNYCentsToQuota(result.PriceCents)
			if err != nil {
				return err
			}
			if err := creditTopUpQuota(tx, userId, quota, nil); err != nil {
				return err
			}
			committedQuotaDelta = int64(quota)
			result.Status = "wallet_credited"
		} else {
			var plan SubscriptionPlan
			if err := common.UnmarshalJsonStr(result.PlanSnapshot, &plan); err != nil {
				return err
			}
			if err := ValidateCNYPlan(&plan); err != nil {
				return err
			}
			if plan.FundingScope != NormalizeFundingScope(result.FundingScope) {
				return errors.New("order funding scope mismatch")
			}
			if !external && result.PriceCents > 0 {
				if plan.AllowBalancePay != nil && !*plan.AllowBalancePay {
					return errors.New("balance payment unavailable")
				}
				quota, err := CNYCentsToQuota(result.PriceCents)
				if err != nil {
					return err
				}
				if user.Quota < quota {
					return errors.New("余额不足，请先充值")
				}
				if err := tx.Model(&User{}).Where("id = ? AND quota >= ?", userId, quota).Update("quota", gorm.Expr("quota - ?", quota)).Error; err != nil {
					return err
				}
				committedQuotaDelta = -int64(quota)
			}
			if result.OldSubscriptionId > 0 {
				if err := tx.Model(&UserSubscription{}).Where("id = ?", result.OldSubscriptionId).Updates(map[string]any{"status": "upgraded", "updated_at": now}).Error; err != nil {
					return err
				}
			}
			price, err := BillingPriceCents(plan.PriceAmount)
			if err != nil {
				return err
			}
			if result.FundingScope == "team" && result.WorkspaceTeamID == 0 {
				var membership WorkspaceMember
				if err := tx.Where("user_id = ?", userId).Limit(1).Find(&membership).Error; err != nil {
					return err
				}
				if membership.TeamID > 0 {
					_, keys, err := LeaveWorkspaceTeamTx(tx, userId, membership.TeamID)
					if err != nil {
						return err
					}
					revokedKeys = append(revokedKeys, keys...)
				}
				name := user.DisplayName
				if strings.TrimSpace(name) == "" {
					name = user.Username
				}
				team, err := CreateWorkspaceTeamTx(tx, userId, name+" 的团队")
				if err != nil {
					return err
				}
				result.WorkspaceTeamID = team.ID
			}
			sub := UserSubscription{WorkspaceTeamID: result.WorkspaceTeamID, UserId: userId, PlanId: plan.Id, AmountTotal: result.CreditedQuota, WeeklyAmount: plan.WeeklyAmount, WeeklyResetAt: now + BillingWeekSeconds, StartTime: now, EndTime: now + result.DurationSeconds, Status: "active", Source: "cny_order", PurchasePriceCents: price, PurchaseTitle: plan.Title, AllowWalletOverflow: result.FundingScope != "team" && (plan.AllowWalletOverflow == nil || *plan.AllowWalletOverflow)}
			if err := tx.Create(&sub).Error; err != nil {
				return err
			}
			result.SubscriptionId = sub.Id
			if result.FundingScope != "team" {
				settings := user.GetSetting()
				settings.BillingPreference = "subscription_first"
				data, err := common.Marshal(settings)
				if err != nil {
					return err
				}
				if err := tx.Model(&User{}).Where("id = ?", userId).Update("setting", string(data)).Error; err != nil {
					return err
				}
				committedSetting = string(data)
			}
			result.Status = "paid"
		}
		result.PaymentMethod, result.CompletedAt = method, now
		return tx.Save(&result).Error
	})
	if err == nil {
		for _, key := range revokedKeys {
			invalidateTokenCacheForMutation(key)
		}
		// Keep pending relay reservations in Redis. Clearing the hash, including
		// on a replayed paid order, would hydrate stale pre-batch database credit.
		if committedQuotaDelta != 0 {
			if cacheErr := cacheIncrUserQuota(userId, committedQuotaDelta); cacheErr != nil {
				common.SysError("billing user quota cache synchronization failed")
			}
		}
		if committedSetting != "" {
			if cacheErr := updateUserSettingCache(userId, committedSetting); cacheErr != nil {
				common.SysError("billing user settings cache synchronization failed")
			}
		}
	}
	return &result, err
}

func ListBillingOrders(userId int) ([]BillingOrder, error) {
	var orders []BillingOrder
	err := DB.Where("user_id = ?", userId).Order("id desc").Limit(50).Find(&orders).Error
	return orders, err
}

func IsBillingEpayMethod(method string) bool { return strings.HasPrefix(method, "epay:") }
