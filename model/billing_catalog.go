package model

import (
	"errors"
	"math"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/shopspring/decimal"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const BillingWeekSeconds int64 = 7 * 24 * 3600
const BillingPeriodSeconds int64 = 4 * BillingWeekSeconds
const BillingCNYPerUSD int64 = 7

func NormalizeFundingScope(scope string) string {
	if scope == "" {
		return "personal"
	}
	return scope
}

// CNY is a ledger denomination, not a live FX quote. Never recompute purchased
// rights using an administrator's later exchange-rate or catalog changes.
func CNYCentsToQuota(cents int64) (int, error) {
	if cents <= 0 || cents > 100000000 {
		return 0, errors.New("invalid CNY amount")
	}
	return common.WalletQuotaFromDecimalStrict(decimal.NewFromInt(cents).
		Mul(decimal.NewFromFloat(common.QuotaPerUnit)).Div(decimal.NewFromInt(100 * BillingCNYPerUSD)).Floor())
}

func BillingPriceCents(amount float64) (int64, error) {
	if math.IsNaN(amount) || math.IsInf(amount, 0) {
		return 0, errors.New("invalid price")
	}
	d := decimal.NewFromFloat(amount).Mul(decimal.NewFromInt(100))
	if d.LessThan(decimal.NewFromInt(1)) || d.GreaterThan(decimal.NewFromInt(1000000)) || !d.Equal(d.Truncate(0)) {
		return 0, errors.New("price must be between CNY 0.01 and 10000, with at most two decimal places")
	}
	return d.IntPart(), nil
}

func (p *SubscriptionPlan) IsCNYWeekly() bool { return p.Currency == "CNY" }

func ValidateCNYPlan(p *SubscriptionPlan) error {
	p.FundingScope = NormalizeFundingScope(p.FundingScope)
	if p.FundingScope != "personal" && p.FundingScope != "team" {
		return errors.New("invalid funding scope")
	}
	if p.FundingScope == "team" {
		if !p.IsCNYWeekly() {
			return errors.New("team plans require CNY weekly checkout")
		}
		if p.AllowWalletOverflow != nil && *p.AllowWalletOverflow {
			return errors.New("team plans cannot use wallet overflow")
		}
		p.AllowWalletOverflow = common.GetPointer(false)
	}
	if !p.IsCNYWeekly() {
		return nil
	}
	if _, err := BillingPriceCents(p.PriceAmount); err != nil {
		return err
	}
	if strings.TrimSpace(p.Title) == "" || len(p.Title) > 128 || len(p.Subtitle) > 255 {
		return errors.New("invalid plan title or subtitle")
	}
	if p.WeeklyAmount <= 0 || p.WeeklyAmount > common.MaxWalletQuota/4 || p.TotalAmount != p.WeeklyAmount*4 {
		return errors.New("28-day total must equal four weekly allowances")
	}
	if p.DurationUnit != SubscriptionDurationDay || p.DurationValue != 28 || p.QuotaResetPeriod != SubscriptionResetNever || p.UpgradeGroup != "" || p.DowngradeGroup != "" {
		return errors.New("CNY plans require 28 days, independent weekly limits and no group discount")
	}
	if p.StripePriceId != "" || p.CreemProductId != "" || p.WaffoPancakeProductId != "" {
		return errors.New("CNY plans use the CNY checkout")
	}
	return nil
}

// Explicit admin action: repeated initialization never overwrites edited plans.
func InitializeCNYPlans() error {
	return DB.Transaction(func(tx *gorm.DB) error {
		// A unique durable marker serializes initialization across app instances.
		// It commits with all six plans, so a failed seed remains retryable.
		claim := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&Option{Key: "billing_cny_catalog_initialized", Value: "1"})
		if claim.Error != nil {
			return claim.Error
		}
		if claim.RowsAffected == 0 {
			return errors.New("CNY catalog already initialized")
		}
		var count int64
		if err := tx.Model(&SubscriptionPlan{}).Where("currency = ?", "CNY").Count(&count).Error; err != nil {
			return err
		}
		if count > 0 {
			return errors.New("CNY catalog already exists")
		}
		for index, item := range []struct {
			title         string
			price, weekly int64
		}{
			{"Lite", 99, 27}, {"Starter", 199, 57}, {"Pro", 399, 120}, {"Max", 899, 280}, {"Ultra", 1799, 580}, {"Scale", 2599, 840},
		} {
			weekly, err := CNYCentsToQuota(item.weekly * 100)
			if err != nil {
				return err
			}
			plan := SubscriptionPlan{Title: item.title, PriceAmount: float64(item.price), Currency: "CNY", DurationUnit: SubscriptionDurationDay, DurationValue: 28, Enabled: true, SortOrder: 100 - index, WeeklyAmount: int64(weekly), TotalAmount: int64(weekly) * 4, QuotaResetPeriod: SubscriptionResetNever, AllowBalancePay: common.GetPointer(true), AllowWalletOverflow: common.GetPointer(true)}
			if err := tx.Create(&plan).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

// The final fractional week of an upgrade extension receives only its
// proportional allowance. A few extra seconds must not unlock a full week.
func (s *UserSubscription) WeeklyLimit() int64 {
	if s.PurchasePriceCents <= 0 || s.WeeklyAmount <= 0 {
		return s.WeeklyAmount
	}
	start := s.WeeklyResetAt - BillingWeekSeconds
	seconds := min(BillingWeekSeconds, max(int64(0), s.EndTime-start))
	q, err := common.WalletQuotaFromDecimalStrict(decimal.NewFromInt(s.WeeklyAmount).Mul(decimal.NewFromInt(seconds)).Div(decimal.NewFromInt(BillingWeekSeconds)).Floor())
	if err != nil {
		return 0
	}
	return int64(q)
}

type BillingUpgradeValue struct {
	RemainingWeeks   int64 `json:"remaining_weeks"`
	ResidualCents    int64 `json:"residual_cents"`
	ExtensionSeconds int64 `json:"extension_seconds"`
}

func CalculateBillingUpgrade(old *UserSubscription, targetPriceCents, now int64) (BillingUpgradeValue, error) {
	var value BillingUpgradeValue
	if old.Status != "active" || old.StartTime > now || old.EndTime <= now || old.PurchasePriceCents <= 0 || targetPriceCents <= old.PurchasePriceCents || targetPriceCents > 1000000 {
		return value, errors.New("only an active paid subscription can upgrade to a higher price")
	}
	// At the exact reset boundary, that new week is already the current week.
	currentEnd := old.StartTime + ((now-old.StartTime)/BillingWeekSeconds+1)*BillingWeekSeconds
	value.RemainingWeeks = max(int64(0), (old.EndTime-currentEnd)/BillingWeekSeconds)
	if value.RemainingWeeks == 0 {
		return value, nil
	}
	value.ResidualCents = decimal.NewFromInt(old.PurchasePriceCents).Mul(decimal.NewFromInt(value.RemainingWeeks)).Div(decimal.NewFromInt(4)).Floor().IntPart()
	value.ExtensionSeconds = decimal.NewFromInt(value.ResidualCents).Mul(decimal.NewFromInt(BillingPeriodSeconds)).Div(decimal.NewFromInt(targetPriceCents)).Floor().IntPart()
	if value.ExtensionSeconds <= 0 || value.ExtensionSeconds >= old.EndTime-old.StartTime {
		return BillingUpgradeValue{}, errors.New("invalid residual duration")
	}
	return value, nil
}
