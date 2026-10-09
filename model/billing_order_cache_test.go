package model

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func setupBillingOrderCacheTest(t *testing.T) {
	t.Helper()
	truncateTables(t)
	resetBatchUpdateTestState(t)
	require.NoError(t, DB.AutoMigrate(&BillingOrder{}, &WorkspaceMember{}, &WorkspaceTeam{}))
	t.Cleanup(func() {
		require.NoError(t, DB.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&BillingOrder{}).Error)
	})
}

func TestBillingPaygoPreservesCachedReservationsAndReplay(t *testing.T) {
	setupBillingOrderCacheTest(t)
	useUserCacheMiniRedis(t)
	common.BatchUpdateEnabled = true
	user := createReserveTestUser(t, 100)
	reserved, err := TryReserveUserQuota(user.Id, 80)
	require.NoError(t, err)
	require.True(t, reserved)
	require.Equal(t, 100, getUserQuotaFromDB(t, user.Id))
	order, err := CreateBillingCheckout(user.Id, BillingCheckoutRequest{Kind: "paygo", AmountCNY: 50, IdempotencyKey: "paygo-cache-reservation-test"})
	require.NoError(t, err)
	_, err = ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
	require.NoError(t, err)
	_, err = FulfillBillingOrder(user.Id, order.TradeNo, "epay:alipay", 5000, true)
	require.NoError(t, err)
	cache, err := GetUserCache(user.Id)
	require.NoError(t, err)
	require.EqualValues(t, 20+order.CreditedQuota, cache.Quota, "the top-up must retain the unflushed reservation")

	reserved, err = TryReserveUserQuota(user.Id, cache.Quota)
	require.NoError(t, err)
	require.True(t, reserved)
	// Both an online callback replay and an authenticated balance replay are
	// idempotent: neither may clear the authoritative cache balance.
	_, err = FulfillBillingOrder(user.Id, order.TradeNo, "epay:alipay", 5000, true)
	require.NoError(t, err)
	_, err = FulfillBillingOrder(user.Id, order.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	reserved, err = TryReserveUserQuota(user.Id, 1)
	require.NoError(t, err)
	require.False(t, reserved, "replay must not rehydrate the unflushed DB balance")
	batchUpdate()
	require.Zero(t, getUserQuotaFromDB(t, user.Id))
}

func TestBillingCNYRejectsUnsupportedLedgerModeBeforeCharging(t *testing.T) {
	setupBillingOrderCacheTest(t)
	user := createReserveTestUser(t, 100000000)
	plan := SubscriptionPlan{Title: "Ledger mode test", Currency: "CNY", PriceAmount: 99, DurationUnit: SubscriptionDurationDay, DurationValue: 28, Enabled: true, WeeklyAmount: 1000, TotalAmount: 4000, QuotaResetPeriod: SubscriptionResetNever}
	require.NoError(t, DB.Create(&plan).Error)
	request := BillingCheckoutRequest{Kind: "subscription", PlanId: plan.Id, IdempotencyKey: "unsupported-ledger-mode-test"}
	common.BatchUpdateEnabled = true
	_, err := CreateBillingCheckout(user.Id, request)
	require.ErrorContains(t, err, "Redis and batch updates")
	common.BatchUpdateEnabled = false
	order, err := CreateBillingCheckout(user.Id, request)
	require.NoError(t, err)
	useUserCacheMiniRedis(t)
	_, err = CreateBillingCheckout(user.Id, BillingCheckoutRequest{Kind: "subscription", PlanId: plan.Id, IdempotencyKey: "unsupported-redis-mode-test"})
	require.ErrorContains(t, err, "Redis and batch updates")
	_, err = ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
	require.ErrorContains(t, err, "Redis and batch updates")
	_, err = FulfillBillingOrder(user.Id, order.TradeNo, "balance", 0, false)
	require.ErrorContains(t, err, "Redis and batch updates")
	require.Equal(t, user.Quota, getUserQuotaFromDB(t, user.Id))
	// A gateway payment already sent before the runtime configuration changes
	// must still become cash credit, rather than unusable subscription rights.
	common.RedisEnabled = false
	_, err = ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
	require.NoError(t, err)
	common.RedisEnabled = true
	paid, err := FulfillBillingOrder(user.Id, order.TradeNo, "epay:alipay", 9900, true)
	require.NoError(t, err)
	require.Equal(t, "wallet_credited", paid.Status)
	require.Zero(t, paid.SubscriptionId)
}
