package controller

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestLegacySubscriptionCheckoutScope(t *testing.T) {
	setupTeamDatabase(t)
	policy := operation_setting.GetPaymentSetting()
	before := *policy
	policy.ComplianceConfirmed = true
	policy.ComplianceTermsVersion = operation_setting.CurrentComplianceTermsVersion
	t.Cleanup(func() { *policy = before })
	user := model.User{Username: "legacy-checkout", AffCode: "legacy-checkout", Status: common.UserStatusEnabled, Group: "default"}
	require.NoError(t, model.DB.Create(&user).Error)
	_, err := model.SetWorkspaceWeeklySubscription(user.Id, 0, 100)
	require.NoError(t, err)
	plan := model.SubscriptionPlan{Title: "Legacy USD", Currency: "USD", PriceAmount: 1.25, DurationUnit: "month", DurationValue: 1, TotalAmount: 777, Enabled: true}
	require.NoError(t, model.DB.Create(&plan).Error)
	for name, handler := range map[string]gin.HandlerFunc{
		"stripe": SubscriptionRequestStripePay, "creem": SubscriptionRequestCreemPay,
		"epay": SubscriptionRequestEpay, "waffo": SubscriptionRequestWaffoPancakePay,
	} {
		t.Run(name, func(t *testing.T) {
			router := gin.New()
			router.POST("/pay", func(c *gin.Context) { c.Set("id", user.Id); handler(c) })
			response := httptest.NewRecorder()
			router.ServeHTTP(response, httptest.NewRequest("POST", "/pay", strings.NewReader(fmt.Sprintf(`{"plan_id":%d}`, plan.Id))))
			require.Contains(t, response.Body.String(), "active subscription already exists", "must reject before any external payment setup")
		})
	}
	require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ?", user.Id).Updates(map[string]any{"start_time": common.GetTimestamp() + 100}).Error)
	require.ErrorIs(t, model.ValidateLegacySubscriptionCheckout(user.Id, &plan), model.ErrSubscriptionScopeOccupied, "future personal rights also occupy the scope")
	require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ?", user.Id).Update("end_time", common.GetTimestamp()-1).Error)
	require.NoError(t, model.ValidateLegacySubscriptionCheckout(user.Id, &plan))
	require.NoError(t, model.DB.Model(&user).Update("quota", common.MaxWalletQuota).Error)
	require.ErrorIs(t, model.ValidateLegacySubscriptionCheckout(user.Id, &plan), model.ErrTopUpQuotaLimitExceeded)
}

func TestLegacySubscriptionPaidConflict(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.AutoMigrate(&model.SubscriptionOrder{}, &model.TopUp{}))
	teamSub, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 100)
	require.NoError(t, err)
	var teamBefore model.UserSubscription
	require.NoError(t, model.DB.First(&teamBefore, teamSub.Id).Error)
	plan := model.SubscriptionPlan{Title: "Legacy USD", Currency: "USD", PriceAmount: 1.25, DurationUnit: "month", DurationValue: 1, TotalAmount: 777, Enabled: true}
	require.NoError(t, model.DB.Create(&plan).Error)
	order := model.SubscriptionOrder{UserId: owner.Id, PlanId: plan.Id, Money: 1.25, TradeNo: "legacy-first", PaymentMethod: model.PaymentMethodStripe, PaymentProvider: model.PaymentProviderStripe, Status: common.TopUpStatusPending}
	require.NoError(t, model.DB.Create(&order).Error)
	// Team rights do not block a first personal subscription.
	require.NoError(t, model.CompleteSubscriptionOrder(order.TradeNo, "", model.PaymentProviderStripe, ""))
	var personal model.UserSubscription
	require.NoError(t, model.DB.First(&personal, "user_id = ? AND workspace_team_id = 0", owner.Id).Error)
	order.Id, order.TradeNo, order.Status = 0, "legacy-conflict", common.TopUpStatusPending
	require.NoError(t, model.DB.Create(&order).Error)
	require.ErrorIs(t, model.CompleteSubscriptionOrder(order.TradeNo, "", model.PaymentProviderEpay, ""), model.ErrPaymentMethodMismatch)
	connection, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		connection.SetMaxOpenConns(8)
	}
	var group sync.WaitGroup
	results := make(chan error, 12)
	for range 12 {
		group.Go(func() { results <- model.CompleteSubscriptionOrder(order.TradeNo, "", model.PaymentProviderStripe, "") })
	}
	group.Wait()
	close(results)
	for err := range results {
		require.NoError(t, err)
	}
	var saved model.User
	require.NoError(t, model.DB.First(&saved, owner.Id).Error)
	require.EqualValues(t, int64(owner.Quota)+625000, saved.Quota, "credit paid price once, never the plan's 777 resource units")
	require.NoError(t, model.DB.First(&order, order.Id).Error)
	require.Equal(t, common.TopUpStatusSuccess, order.Status)
	require.EqualValues(t, 625000, order.WalletCreditQuota)
	var personalAfter model.UserSubscription
	require.NoError(t, model.DB.First(&personalAfter, personal.Id).Error)
	require.Equal(t, personal, personalAfter)
	var teamAfter model.UserSubscription
	require.NoError(t, model.DB.First(&teamAfter, teamSub.Id).Error)
	require.Equal(t, teamBefore, teamAfter)
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.Zero(t, account.Quota)
	var count int64
	require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ? AND workspace_team_id = 0", owner.Id).Count(&count).Error)
	require.EqualValues(t, 1, count)
	require.NoError(t, model.DB.Model(&model.TopUp{}).Where("trade_no = ?", order.TradeNo).Count(&count).Error)
	require.EqualValues(t, 1, count)
	// A full wallet preserves the paid order for reconciliation; no partial
	// credit, duplicate rights or falsely successful receipt can commit.
	require.NoError(t, model.DB.Model(&owner).Update("quota", common.MaxWalletQuota).Error)
	order.Id, order.TradeNo, order.Status, order.WalletCreditQuota = 0, "legacy-full-wallet", common.TopUpStatusPending, 0
	require.NoError(t, model.DB.Create(&order).Error)
	require.ErrorIs(t, model.CompleteSubscriptionOrder(order.TradeNo, "", model.PaymentProviderStripe, ""), model.ErrTopUpQuotaLimitExceeded)
	require.NoError(t, model.DB.First(&order, order.Id).Error)
	require.Equal(t, common.TopUpStatusPending, order.Status)
	require.Zero(t, order.WalletCreditQuota)
	require.NoError(t, model.DB.Model(&model.TopUp{}).Where("trade_no = ?", order.TradeNo).Count(&count).Error)
	require.Zero(t, count)
}
