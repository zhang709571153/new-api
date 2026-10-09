package controller

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
	"gorm.io/gorm"
)

func TestWorkspaceTeamSubscriptionOnly(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("quota", 9000).Error)
	personal, err := model.SetWorkspaceSubscription(owner.Id, 1000, 1000)
	require.NoError(t, err)
	var sub *model.UserSubscription
	for _, state := range []string{"missing", "future", "expired", "cancelled", "week empty", "estimate exceeds week", "no weekly contract"} {
		t.Run(state, func(t *testing.T) {
			if state != "missing" {
				if sub == nil {
					sub, err = model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
					require.NoError(t, err)
				}
				now := common.GetTimestamp()
				changes := map[string]any{"allow_wallet_overflow": true, "status": "active", "start_time": now, "end_time": now + model.BillingPeriodSeconds, "weekly_used": 0, "amount_used": 0}
				switch state {
				case "future":
					changes["start_time"] = now + 100
				case "expired":
					changes["end_time"] = now - 1
				case "cancelled":
					changes["status"] = "cancelled"
				case "week empty":
					changes["weekly_used"] = 100
				case "estimate exceeds week":
					changes["weekly_used"] = 99
				case "no weekly contract":
					changes["weekly_amount"] = 0
				}
				require.NoError(t, model.DB.Model(sub).Updates(changes).Error)
			}
			require.Equal(t, 403, fundingRequest(t, key, "team-no-paygo", 2, 2).Code)
			var account model.WorkspaceTeamAccount
			require.NoError(t, model.DB.First(&account, team.ID).Error)
			require.EqualValues(t, 9000, account.Quota)
			require.NoError(t, model.DB.First(&owner, owner.Id).Error)
			require.NoError(t, model.DB.First(&member, member.Id).Error)
			require.Equal(t, 1000, owner.Quota)
			require.Equal(t, 2000, member.Quota)
			require.NoError(t, model.DB.First(personal, personal.Id).Error)
			require.Zero(t, personal.AmountUsed)
			available, wallet, _, err := model.WorkspaceTeamFunding(team)
			require.NoError(t, err)
			require.Zero(t, wallet, "legacy balance is not spendable PAYGO")
			if state != "estimate exceeds week" {
				require.Zero(t, available)
			}
		})
	}
}

func TestWorkspaceSubscriptionCancellationScope(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 100)
	require.NoError(t, err)
	router := gin.New()
	router.POST("/subscriptions/:id/invalidate", func(c *gin.Context) { c.Set("role", common.RoleRootUser); AdminInvalidateUserSubscription(c) })
	call := func(body string) *httptest.ResponseRecorder {
		res := httptest.NewRecorder()
		router.ServeHTTP(res, httptest.NewRequest("POST", fmt.Sprintf("/subscriptions/%d/invalidate", sub.Id), strings.NewReader(body)))
		return res
	}
	for _, body := range []string{
		fmt.Sprintf(`{"expected_scope":{"user_id":%d,"workspace_team_id":0}}`, owner.Id),
		fmt.Sprintf(`{"expected_scope":{"user_id":%d,"workspace_team_id":%d}}`, owner.Id+1000, team.ID),
		fmt.Sprintf(`{"expected_scope":{"user_id":%d}}`, owner.Id),
		`{"expected_scope":{}}`, `{"expected_scope":{"user_id":0,"workspace_team_id":0}}`,
	} {
		response := call(body)
		require.False(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
		require.NoError(t, model.DB.First(sub, sub.Id).Error)
		require.Equal(t, "active", sub.Status)
	}
	response := call(fmt.Sprintf(`{"expected_scope":{"user_id":%d,"workspace_team_id":%d}}`, owner.Id, team.ID))
	require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Equal(t, "cancelled", sub.Status)
	// Generic legacy callers without a scope object stay supported.
	response = call("")
	require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
}

func TestWorkspaceTeamSubscriptionConflict(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	first, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	second := *first
	second.Id = 0
	second.EndTime++
	require.NoError(t, model.DB.Create(&second).Error)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("quota", 9000).Error)
	for _, future := range []bool{false, true} {
		start := common.GetTimestamp() - 10
		if future {
			start = common.GetTimestamp() + 100
		}
		require.NoError(t, model.DB.Model(&second).Update("start_time", start).Error)
		require.Equal(t, 403, fundingRequest(t, key, fmt.Sprintf("conflict-%v", future), 1, 1).Code)
		_, err = model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 200)
		require.ErrorContains(t, err, "multiple active team subscriptions")
		_, err = model.UpdateWorkspaceSubscriptionWeeklyLimit(owner.Id, team.ID, first.Id, 200)
		require.ErrorContains(t, err, "multiple active team subscriptions")
		available, wallet, summaries, err := model.WorkspaceTeamFunding(team)
		require.NoError(t, err)
		require.Zero(t, available)
		require.Zero(t, wallet)
		require.Len(t, summaries, 2)
	}
	session, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "conflict-test")
	require.NoError(t, err)
	r := gin.New()
	r.GET("/workspace", middleware.UserAuth(), WorkspaceSessionRequired, GetWorkspace)
	r.GET("/team", middleware.UserAuth(), WorkspaceSessionRequired, GetWorkspaceTeam)
	r.POST("/key/reveal", middleware.UserAuth(), WorkspaceSessionRequired, RevealWorkspaceKey)
	r.GET("/api/usage/token/", middleware.TokenAuthReadOnly(), GetTokenUsage)
	for _, path := range []string{"/workspace?scope=team", "/team"} {
		req := httptest.NewRequest("GET", path, nil)
		req.Header.Set("Authorization", "Bearer "+session.AccessToken)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		require.Equal(t, 200, res.Code)
		require.Contains(t, res.Body.String(), `"subscription_conflict":true`)
	}
	for _, args := range [][3]string{{"POST", "/key/reveal?scope=team", session.AccessToken}, {"GET", "/api/usage/token/", model.WorkspaceKeyText(key)}} {
		req := httptest.NewRequest(args[0], args[1], strings.NewReader(`{}`))
		req.Header.Set("Authorization", "Bearer "+args[2])
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		require.Equal(t, 200, res.Code)
		require.NotContains(t, res.Body.String(), `"success":false`)
	}
	require.NoError(t, model.DB.First(first, first.Id).Error)
	require.EqualValues(t, 100, first.WeeklyAmount)
	require.Zero(t, first.AmountUsed)
	// Cancellation resolves the conflict without discarding historical rows.
	_, err = model.AdminInvalidateUserSubscription(second.Id)
	require.NoError(t, err)
	require.Equal(t, 200, fundingRequest(t, key, "resolved-conflict", 10, 10).Code)
	summary, err := service.GetWorkspaceSummaryForScope(member.Id, "team")
	require.NoError(t, err)
	encoded, err := common.Marshal(summary)
	require.NoError(t, err)
	require.Contains(t, string(encoded), `"subscription_conflict":false`)
	require.NoError(t, model.DB.First(&second, second.Id).Error)
	require.Equal(t, "cancelled", second.Status)
	// Historical duplicate personal rows are diagnosed without spending PAYGO.
	var personalID int
	for i := range 2 {
		personal := model.UserSubscription{UserId: owner.Id, Source: "workspace_admin", Status: "active", StartTime: common.GetTimestamp() - 100, EndTime: common.GetTimestamp() + int64(1000+i), AmountTotal: 400, WeeklyAmount: 100, WeeklyResetAt: common.GetTimestamp() + model.BillingWeekSeconds, AllowWalletOverflow: true}
		if i == 0 {
			personal.WeeklyUsed = 100
		}
		require.NoError(t, model.DB.Create(&personal).Error)
		personalID = personal.Id
	}
	_, err = model.UpdateSubscriptionFirstFunding(owner.Id, "personal-conflict", 10, "reserve")
	require.ErrorContains(t, err, "multiple active subscriptions")
	_, err = model.UpdateWorkspaceSubscriptionWeeklyLimit(owner.Id, 0, personalID, 200)
	require.ErrorContains(t, err, "multiple active subscriptions")
	_, err = model.PreConsumeUserSubscription("personal-legacy-conflict", owner.Id, "model", 0, 1)
	require.ErrorContains(t, err, "multiple active subscriptions")
	personalKey, err := model.GetWorkspaceKeyForScope(owner.Id, "personal", true)
	require.NoError(t, err)
	require.Equal(t, 403, fundingRequest(t, personalKey, "personal-http-conflict", 1, 1).Code)
	personalView, err := service.GetWorkspaceSummaryForScope(owner.Id, "personal")
	require.NoError(t, err)
	require.True(t, personalView.SubscriptionConflict)
	require.Zero(t, personalView.BalanceUSD)
	require.NotNil(t, personalView.APIKey)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
}

func TestWorkspacePersonalSubscriptionGrantUniqueness(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	personal, err := model.SetWorkspaceWeeklySubscription(owner.Id, 0, 100)
	require.NoError(t, err)
	teamSub, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 200)
	require.NoError(t, err)
	require.NotEqual(t, personal.Id, teamSub.Id)
	require.Equal(t, team.ID, teamSub.WorkspaceTeamID)
	require.NoError(t, model.DB.Model(personal).Updates(map[string]any{"amount_used": 500, "weekly_used": 0}).Error)
	reservation, err := model.UpdateSubscriptionFirstFunding(owner.Id, "personal-weekly-no-hidden-total-cap", 10, "reserve")
	require.NoError(t, err)
	require.EqualValues(t, 10, reservation.Record.PreConsumed)
	require.Zero(t, reservation.Record.WalletPreConsumed)
	_, err = model.UpdateSubscriptionFirstFunding(owner.Id, "personal-weekly-no-hidden-total-cap", 10, "settle")
	require.NoError(t, err)
	// Generic administrator binds and balance purchases must not bypass the
	// same-scope rule, even when they use a different plan.
	plan := model.SubscriptionPlan{Title: "unique legacy grant", Currency: "USD", PriceAmount: 0.001, DurationUnit: model.SubscriptionDurationMonth, DurationValue: 1, TotalAmount: 400, Enabled: true, AllowBalancePay: common.GetPointer(true)}
	require.NoError(t, model.DB.Create(&plan).Error)
	_, err = model.AdminBindSubscription(owner.Id, plan.Id, "")
	require.Error(t, err)
	err = model.PurchaseSubscriptionWithBalance(owner.Id, plan.Id)
	require.Error(t, err)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
	// Serialize grants from two entry points against the same personal owner.
	sqlDB, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		sqlDB.SetMaxOpenConns(8)
	}
	start := make(chan struct{})
	results := make(chan error, 2)
	var workers sync.WaitGroup
	workers.Go(func() { <-start; _, err := model.AdminBindSubscription(member.Id, plan.Id, ""); results <- err })
	workers.Go(func() { <-start; _, err := model.SetWorkspaceWeeklySubscription(member.Id, 0, 100); results <- err })
	close(start)
	workers.Wait()
	close(results)
	successes := 0
	for err := range results {
		if err == nil {
			successes++
		}
	}
	require.Equal(t, 1, successes)
	var rows []model.UserSubscription
	require.NoError(t, model.DB.Where("user_id = ? AND workspace_team_id = 0 AND status = ? AND end_time > ?", member.Id, "active", common.GetTimestamp()).Find(&rows).Error)
	require.Len(t, rows, 1)
	// Precise administrator changes can lower an allowance without creating a
	// replacement entitlement or altering dates, including generic grants.
	updated, err := model.UpdateWorkspaceSubscriptionWeeklyLimit(member.Id, 0, rows[0].Id, 50)
	require.NoError(t, err)
	require.Equal(t, rows[0].Id, updated.Id)
	require.Equal(t, rows[0].EndTime, updated.EndTime)
	require.NoError(t, model.DB.Model(updated).Update("end_time", common.GetTimestamp()-1).Error)
	_, err = model.AdminBindSubscription(member.Id, plan.Id, "")
	require.NoError(t, err, "expired history does not occupy the active slot")
}

func TestBillingTeamOwnershipPurchase(t *testing.T) {
	buyer, personalPlans := billingFixture(t)
	teamPlan := personalPlans[1]
	teamPlan.Id, teamPlan.FundingScope, teamPlan.Title = 0, "team", "Team contract"
	teamPlan.AllowWalletOverflow = common.GetPointer(false)
	require.NoError(t, model.DB.Create(&teamPlan).Error)
	personal, err := model.SetWorkspaceWeeklySubscription(buyer.Id, 0, 100)
	require.NoError(t, err)
	personalKey, err := model.GetWorkspaceKeyForScope(buyer.Id, "personal", true)
	require.NoError(t, err)
	// A pending checkout must not grant ownership or change existing funding.
	req := model.BillingCheckoutRequest{Kind: "subscription", FundingScope: "team", PlanId: teamPlan.Id, IdempotencyKey: "team-first-purchase-key"}
	order, err := model.CreateBillingCheckout(buyer.Id, req)
	require.NoError(t, err)
	_, beforeTeam, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.Nil(t, beforeTeam)
	paid, err := model.FulfillBillingOrder(buyer.Id, order.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	member, team, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.NotNil(t, team)
	require.Equal(t, buyer.Id, team.OwnerUserID)
	require.Equal(t, 1, team.FundingVersion)
	require.Equal(t, team.ID, paid.WorkspaceTeamID)
	var sub model.UserSubscription
	require.NoError(t, model.DB.First(&sub, paid.SubscriptionId).Error)
	require.Equal(t, team.ID, sub.WorkspaceTeamID)
	require.False(t, sub.AllowWalletOverflow)
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	require.Zero(t, personal.WorkspaceTeamID)
	require.Zero(t, personal.AmountUsed)
	afterKey, err := model.GetWorkspaceKeyForScope(buyer.Id, "personal", true)
	require.NoError(t, err)
	require.Equal(t, personalKey.Key, afterKey.Key)
	for range 2 {
		_, err = model.FulfillBillingOrder(buyer.Id, order.TradeNo, "balance", 0, false)
		require.NoError(t, err)
	}
	var count int64
	require.NoError(t, model.DB.Model(&model.WorkspaceTeam{}).Where("owner_user_id = ?", buyer.Id).Count(&count).Error)
	require.EqualValues(t, 1, count)
	// No second initial purchase, cross-scope key reuse, PAYGO, same-tier or downgrade.
	for i, invalid := range []model.BillingCheckoutRequest{
		{Kind: "subscription", FundingScope: "team", PlanId: teamPlan.Id},
		{Kind: "paygo", FundingScope: "team", AmountCNY: 50},
		{Kind: "upgrade", FundingScope: "team", PlanId: teamPlan.Id, OldSubscriptionId: sub.Id},
		{Kind: "upgrade", FundingScope: "personal", PlanId: personalPlans[3].Id, OldSubscriptionId: sub.Id},
		{Kind: "upgrade", FundingScope: "team", PlanId: personalPlans[3].Id, OldSubscriptionId: sub.Id},
	} {
		invalid.IdempotencyKey = fmt.Sprintf("team-invalid-scope-%d-key", i)
		_, err = model.CreateBillingCheckout(buyer.Id, invalid)
		require.Error(t, err)
	}
	upgradePlan := personalPlans[3]
	upgradePlan.Id, upgradePlan.FundingScope, upgradePlan.AllowWalletOverflow = 0, "team", common.GetPointer(false)
	require.NoError(t, model.DB.Create(&upgradePlan).Error)
	upgrade, err := model.CreateBillingCheckout(buyer.Id, model.BillingCheckoutRequest{Kind: "upgrade", FundingScope: "team", PlanId: upgradePlan.Id, OldSubscriptionId: sub.Id, IdempotencyKey: "team-upgrade-purchase-key"})
	require.NoError(t, err)
	require.Positive(t, upgrade.ResidualCents)
	upgraded, err := model.FulfillBillingOrder(buyer.Id, upgrade.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	require.Equal(t, team.ID, upgraded.WorkspaceTeamID)
	afterMember, afterTeam, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.Equal(t, team.ID, afterTeam.ID)
	require.Equal(t, member.TokenID, afterMember.TokenID)
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.Equal(t, "upgraded", sub.Status)
	_, err = model.CreateBillingCheckout(buyer.Id, model.BillingCheckoutRequest{Kind: "upgrade", FundingScope: "team", PlanId: teamPlan.Id, OldSubscriptionId: upgraded.SubscriptionId, IdempotencyKey: "team-downgrade-rejected"})
	require.Error(t, err)
	require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("workspace_team_id = ? AND status = ? AND end_time > ?", team.ID, "active", common.GetTimestamp()).Count(&count).Error)
	require.EqualValues(t, 1, count)
	// Catalog exposes each owner's exact scope, not a team member's pool.
	router := gin.New()
	router.GET("/catalog", func(c *gin.Context) { c.Set("id", buyer.Id); BillingCatalog(c) })
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest("GET", "/catalog", nil))
	require.EqualValues(t, personal.Id, gjson.Get(response.Body.String(), "data.current_subscription.id").Int())
	require.EqualValues(t, upgraded.SubscriptionId, gjson.Get(response.Body.String(), "data.current_team_subscription.id").Int())
	require.Len(t, gjson.Get(response.Body.String(), "data.plans").Array(), 6)
	require.Len(t, gjson.Get(response.Body.String(), "data.team_plans").Array(), 2)
}

func TestBillingTeamMemberPaymentConcurrency(t *testing.T) {
	buyer, plans := billingFixture(t)
	oldOwner := model.User{Username: "paid-old-owner", AffCode: "paid-old-owner", Status: common.UserStatusEnabled, Group: "default"}
	require.NoError(t, model.DB.Create(&oldOwner).Error)
	oldTeam, err := model.CreateWorkspaceTeam(oldOwner.Id, "old team")
	require.NoError(t, err)
	oldSub, err := model.SetWorkspaceWeeklySubscription(oldOwner.Id, oldTeam.ID, 100)
	require.NoError(t, err)
	invite, _, err := model.CreateWorkspaceInvite(oldOwner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(buyer.Id, invite))
	oldMember, _, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	var oldKey model.Token
	require.NoError(t, model.DB.First(&oldKey, oldMember.TokenID).Error)
	personalKey, err := model.GetWorkspaceKeyForScope(buyer.Id, "personal", true)
	require.NoError(t, err)
	teamPlan := plans[0]
	teamPlan.Id, teamPlan.FundingScope, teamPlan.AllowWalletOverflow = 0, "team", common.GetPointer(false)
	require.NoError(t, model.DB.Create(&teamPlan).Error)
	var quotes []*model.BillingOrder
	for i := range 2 {
		quote, err := model.CreateBillingCheckout(buyer.Id, model.BillingCheckoutRequest{FundingScope: "team", Kind: "subscription", PlanId: teamPlan.Id, IdempotencyKey: fmt.Sprintf("member-team-order-%d-key", i)})
		require.NoError(t, err)
		_, err = model.ClaimBillingEpay(buyer.Id, quote.TradeNo, "epay:alipay")
		require.NoError(t, err)
		quotes = append(quotes, quote)
	}
	// Invalid payment is atomic: no owner role, membership or key changes.
	_, err = model.FulfillBillingOrder(buyer.Id, quotes[0].TradeNo, "epay:alipay", quotes[0].PriceCents-1, true)
	require.Error(t, err)
	stillMember, stillTeam, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.Equal(t, oldTeam.ID, stillTeam.ID)
	require.Equal(t, oldMember.TokenID, stillMember.TokenID)
	// A storage failure after provision must roll back membership, keys and
	// entitlement together, so a payment callback can safely retry.
	failureHook := "team-payment-grant-failure"
	require.NoError(t, model.DB.Callback().Create().Before("gorm:create").Register(failureHook, func(tx *gorm.DB) {
		if tx.Statement.Schema != nil && tx.Statement.Schema.Table == "user_subscriptions" {
			tx.AddError(fmt.Errorf("forced entitlement failure"))
		}
	}))
	_, err = model.FulfillBillingOrder(buyer.Id, quotes[0].TradeNo, "epay:alipay", quotes[0].PriceCents, true)
	require.ErrorContains(t, err, "forced entitlement failure")
	require.NoError(t, model.DB.Callback().Create().Remove(failureHook))
	stillMember, stillTeam, err = model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.Equal(t, oldTeam.ID, stillTeam.ID)
	require.Equal(t, oldMember.TokenID, stillMember.TokenID)
	require.NoError(t, model.DB.First(&oldKey, oldKey.Id).Error)
	require.Equal(t, common.TokenStatusEnabled, oldKey.Status)
	sqlDB, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		sqlDB.SetMaxOpenConns(8)
	}
	// Exercise the first legacy member-cap initialization against checkout's
	// lifecycle locks. A departing member may be admitted before departure or
	// rejected after it; either outcome must leave an intact refundable ledger.
	require.NoError(t, model.DB.Model(&model.WorkspaceMember{}).Where("user_id = ?", buyer.Id).Update("weekly_quota", nil).Error)
	require.NoError(t, model.DB.Model(&oldKey).Update("remain_quota", 10).Error)
	fundingResult := make(chan error, 1)
	start := make(chan struct{})
	results := make(chan error, 4)
	var workers sync.WaitGroup
	workers.Go(func() {
		<-start
		_, err := model.UpdateWorkspaceTeamFunding(oldOwner.Id, oldTeam.ID, "checkout-funding-race", 5, "reserve", oldKey.Id)
		fundingResult <- err
	})
	for i := range 4 {
		workers.Go(func() {
			<-start
			quote := quotes[i%2]
			_, err := model.FulfillBillingOrder(buyer.Id, quote.TradeNo, "epay:alipay", quote.PriceCents, true)
			results <- err
		})
	}
	close(start)
	workers.Wait()
	close(results)
	// A membership change while acquiring locks is retryable; provider replay
	// reaches the immutable terminal state without charging twice.
	for range results {
	}
	if reserveErr := <-fundingResult; reserveErr == nil {
		_, err := model.UpdateWorkspaceTeamFunding(oldOwner.Id, oldTeam.ID, "checkout-funding-race", 0, "refund", oldKey.Id)
		require.NoError(t, err)
	} else {
		require.ErrorIs(t, reserveErr, model.ErrWorkspaceAccess)
	}
	for _, quote := range quotes {
		_, err := model.FulfillBillingOrder(buyer.Id, quote.TradeNo, "epay:alipay", quote.PriceCents, true)
		require.NoError(t, err)
	}
	member, team, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.Equal(t, buyer.Id, team.OwnerUserID)
	require.NotEqual(t, oldTeam.ID, team.ID)
	require.NotEqual(t, oldMember.TokenID, member.TokenID)
	require.NoError(t, model.DB.Unscoped().First(&oldKey, oldKey.Id).Error)
	require.Equal(t, common.TokenStatusDisabled, oldKey.Status)
	afterKey, err := model.GetWorkspaceKeyForScope(buyer.Id, "personal", true)
	require.NoError(t, err)
	require.Equal(t, personalKey.Key, afterKey.Key)
	require.NoError(t, model.DB.First(oldSub, oldSub.Id).Error)
	require.Zero(t, oldSub.AmountUsed)
	orders, err := model.ListBillingOrders(buyer.Id)
	require.NoError(t, err)
	paid, credited := 0, 0
	for _, order := range orders {
		if order.Status == "paid" {
			paid++
		}
		if order.Status == "wallet_credited" {
			credited++
		}
	}
	require.Equal(t, 1, paid)
	require.Equal(t, 1, credited)
	credit, err := model.CNYCentsToQuota(quotes[0].PriceCents)
	require.NoError(t, err)
	var after model.User
	require.NoError(t, model.DB.First(&after, buyer.Id).Error)
	require.Equal(t, buyer.Quota+credit, after.Quota, "only the redundant paid quote returns personal cash credit")
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.Zero(t, account.Quota)
}

func TestBillingFundingScopeSchemaUpgrade(t *testing.T) {
	buyer, plans := billingFixture(t)
	quote, err := model.CreateBillingCheckout(buyer.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[0].Id, IdempotencyKey: "legacy-scope-upgrade-quote"})
	require.NoError(t, err)
	// Reconstruct the immediately preceding schema and plan snapshot, then run
	// current additive migrations twice against real database engines.
	var snapshot map[string]any
	require.NoError(t, common.UnmarshalJsonStr(quote.PlanSnapshot, &snapshot))
	delete(snapshot, "funding_scope")
	encoded, err := common.Marshal(snapshot)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(quote).Update("plan_snapshot", string(encoded)).Error)
	require.NoError(t, model.DB.Migrator().DropColumn(&model.SubscriptionPlan{}, "FundingScope"))
	require.NoError(t, model.DB.Migrator().DropColumn(&model.BillingOrder{}, "FundingScope"))
	require.NoError(t, model.DB.Migrator().DropColumn(&model.BillingOrder{}, "WorkspaceTeamID"))
	for range 2 {
		require.NoError(t, model.DB.AutoMigrate(&model.SubscriptionPlan{}, &model.BillingOrder{}))
	}
	stored, err := model.GetBillingOrder(buyer.Id, quote.TradeNo)
	require.NoError(t, err)
	require.Equal(t, "personal", model.NormalizeFundingScope(stored.FundingScope))
	require.Zero(t, stored.WorkspaceTeamID)
	require.Equal(t, quote.PriceCents, stored.PriceCents)
	paid, err := model.FulfillBillingOrder(buyer.Id, quote.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	var sub model.UserSubscription
	require.NoError(t, model.DB.First(&sub, paid.SubscriptionId).Error)
	require.Zero(t, sub.WorkspaceTeamID)
	require.Equal(t, plans[0].WeeklyAmount, sub.WeeklyAmount)
	_, owned, err := model.FindWorkspaceMembership(buyer.Id)
	require.NoError(t, err)
	require.Nil(t, owned)
	// Upgrading the schema must preserve request/trade uniqueness.
	duplicate := *stored
	duplicate.Id = 0
	require.Error(t, model.DB.Create(&duplicate).Error)
}

func TestBillingTeamPlanAdministration(t *testing.T) {
	_, plans := billingFixture(t)
	router := gin.New()
	router.POST("/plans", AdminCreateSubscriptionPlan)
	router.PUT("/plans/:id", AdminUpdateSubscriptionPlan)
	call := func(method, path string, plan model.SubscriptionPlan) *httptest.ResponseRecorder {
		body, err := common.Marshal(AdminUpsertSubscriptionPlanRequest{Plan: plan})
		require.NoError(t, err)
		request := httptest.NewRequest(method, path, strings.NewReader(string(body)))
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		return response
	}
	teamPlan := plans[0]
	teamPlan.Id, teamPlan.Title, teamPlan.FundingScope = 0, "explicit team catalog", "team"
	teamPlan.AllowWalletOverflow = common.GetPointer(false)
	response := call("POST", "/plans", teamPlan)
	require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
	var stored model.SubscriptionPlan
	require.NoError(t, model.DB.Where("title = ?", teamPlan.Title).First(&stored).Error)
	require.Equal(t, "team", stored.FundingScope)
	require.NotNil(t, stored.AllowWalletOverflow)
	require.False(t, *stored.AllowWalletOverflow)
	for _, scope := range []string{"personal", "unknown"} {
		changed := stored
		changed.FundingScope = scope
		require.False(t, gjson.Get(call("PUT", fmt.Sprintf("/plans/%d", stored.Id), changed).Body.String(), "success").Bool())
	}
	for _, invalid := range []string{"overflow", "currency"} {
		changed := teamPlan
		if invalid == "overflow" {
			changed.AllowWalletOverflow = common.GetPointer(true)
		} else {
			changed.Currency = "USD"
		}
		require.False(t, gjson.Get(call("POST", "/plans", changed).Body.String(), "success").Bool())
	}
	personal := plans[0]
	personal.FundingScope, personal.AllowWalletOverflow = "team", common.GetPointer(false)
	require.False(t, gjson.Get(call("PUT", fmt.Sprintf("/plans/%d", personal.Id), personal).Body.String(), "success").Bool())
	var old model.SubscriptionPlan
	require.NoError(t, model.DB.First(&old, personal.Id).Error)
	require.Equal(t, "personal", model.NormalizeFundingScope(old.FundingScope))
}

func TestWorkspaceTeamWeeklyUsageAdministration(t *testing.T) {
	owner, _, team, key := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 100)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"amount_used": 20, "weekly_used": 20}).Error)
	var beforeKey model.Token
	require.NoError(t, model.DB.First(&beforeKey, key.Id).Error)
	router := gin.New()
	role := common.RoleRootUser
	router.PUT("/users/:id", func(c *gin.Context) { c.Set("role", role); AdminSetWorkspaceSubscription(c) })
	expectedResetAt := sub.WeeklyResetAt
	request := func(used string, teamID, subID int) *httptest.ResponseRecorder {
		res := httptest.NewRecorder()
		body := fmt.Sprintf(`{"team_id":%d,"subscription_id":%d,"weekly_used_usd":%s,"weekly_reset_at":%d}`, teamID, subID, used, expectedResetAt)
		router.ServeHTTP(res, httptest.NewRequest("PUT", fmt.Sprintf("/users/%d", owner.Id), strings.NewReader(body)))
		return res
	}
	response := request("0", team.ID, sub.Id)
	require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Zero(t, sub.WeeklyUsed)
	require.EqualValues(t, 20, sub.AmountUsed)
	var afterKey model.Token
	require.NoError(t, model.DB.First(&afterKey, key.Id).Error)
	require.Equal(t, beforeKey.UsedQuota, afterKey.UsedQuota)
	require.Equal(t, beforeKey.RemainQuota, afterKey.RemainQuota)
	for _, value := range []string{"-1", "99999999999999999999999"} {
		require.False(t, gjson.Get(request(value, team.ID, sub.Id).Body.String(), "success").Bool())
	}
	role = common.RoleAdminUser
	require.False(t, gjson.Get(request("0", team.ID, sub.Id).Body.String(), "success").Bool())
	role = common.RoleRootUser
	require.False(t, gjson.Get(request("0", 0, sub.Id).Body.String(), "success").Bool())
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "admin-usage-inflight", 10, "reserve")
	require.NoError(t, err)
	response = request("0", team.ID, sub.Id)
	require.False(t, gjson.Get(response.Body.String(), "success").Bool())
	require.Contains(t, response.Body.String(), "仍有请求结算中")
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "admin-usage-inflight", 0, "refund")
	require.NoError(t, err)
	// An explicit over-limit used count is valid; it blocks new work until reset.
	require.True(t, gjson.Get(request("1", team.ID, sub.Id).Body.String(), "success").Bool())
	require.Equal(t, 403, fundingRequest(t, key, "admin-usage-over-limit", 1, 1).Code)
	// Resetting weekly usage never rewrites the cumulative ledger, even after
	// manual changes allow cumulative usage to exceed the displayed period total.
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"amount_used": 500, "weekly_reset_at": common.GetTimestamp() - 1}).Error)
	response = request("0", team.ID, sub.Id)
	require.False(t, gjson.Get(response.Body.String(), "success").Bool())
	require.Contains(t, response.Body.String(), "周期已变化")
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, common.QuotaPerUnit, sub.WeeklyUsed, "stale reset cannot alter the stored counter")
	sub.RefreshWeeklyWindow(common.GetTimestamp())
	expectedResetAt = sub.WeeklyResetAt
	require.True(t, gjson.Get(request("0", team.ID, sub.Id).Body.String(), "success").Bool())
	require.Equal(t, 200, fundingRequest(t, key, "admin-usage-new-week", 5, 5).Code)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 505, sub.AmountUsed)
	require.EqualValues(t, 5, sub.WeeklyUsed)
}

func TestWorkspaceTeamSubscriptionConcurrentGrant(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	sqlDB, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		sqlDB.SetMaxOpenConns(8)
	}
	type outcome struct {
		id  int
		err error
	}
	results := make(chan outcome, 8)
	start := make(chan struct{})
	var group sync.WaitGroup
	for range 8 {
		group.Go(func() {
			<-start
			sub, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 100)
			id := 0
			if sub != nil {
				id = sub.Id
			}
			results <- outcome{id, err}
		})
	}
	close(start)
	group.Wait()
	close(results)
	var id int
	for result := range results {
		require.NoError(t, result.err)
		if id == 0 {
			id = result.id
		}
		require.Equal(t, id, result.id, "concurrent grants update one entitlement")
	}
	var subs []model.UserSubscription
	require.NoError(t, model.DB.Where("workspace_team_id = ?", team.ID).Find(&subs).Error)
	require.Len(t, subs, 1)
	before := subs[0]
	_, err = model.UpdateWorkspaceSubscriptionWeeklyLimit(member.Id, team.ID, id, 200)
	require.Error(t, err)
	personal, err := model.SetWorkspaceSubscription(owner.Id, 400, 100)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceSubscriptionWeeklyLimit(owner.Id, team.ID, personal.Id, 200)
	require.Error(t, err)
	updated, err := model.UpdateWorkspaceSubscriptionWeeklyLimit(owner.Id, team.ID, id, 200)
	require.NoError(t, err)
	require.Equal(t, id, updated.Id)
	require.EqualValues(t, 800, updated.AmountTotal)
	require.Equal(t, before.EndTime, updated.EndTime)
	require.NoError(t, model.DB.Model(updated).Update("end_time", common.GetTimestamp()-1).Error)
	next, err := model.SetWorkspaceWeeklySubscription(owner.Id, team.ID, 300)
	require.NoError(t, err)
	require.NotEqual(t, id, next.Id)
	var old model.UserSubscription
	require.NoError(t, model.DB.First(&old, id).Error)
	require.EqualValues(t, 800, old.AmountTotal)
	require.NoError(t, model.DB.Where("workspace_team_id = ? AND status = ? AND end_time > ?", team.ID, "active", common.GetTimestamp()).Find(&subs).Error)
	require.Len(t, subs, 1)
	require.Equal(t, next.Id, subs[0].Id)
}

func TestWorkspaceTeamSubscriptionMigrationConflict(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.Delete(&model.WorkspaceTeamAccount{}, team.ID).Error)
	require.NoError(t, model.DB.Model(team).Update("funding_version", 0).Error)
	first, err := model.SetWorkspaceSubscription(owner.Id, 400, 100)
	require.NoError(t, err)
	second := *first
	second.Id = 0
	require.NoError(t, model.DB.Create(&second).Error)
	require.Error(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{first.Id, second.Id}, 0))
	require.NoError(t, model.DB.First(first, first.Id).Error)
	require.Zero(t, first.WorkspaceTeamID)
	require.NoError(t, model.DB.Model(&second).Update("workspace_team_id", team.ID).Error)
	require.Error(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{first.Id}, 0), "target already has an unexpired entitlement")
	require.NoError(t, model.DB.First(first, first.Id).Error)
	require.Zero(t, first.WorkspaceTeamID)
	var receipts int64
	require.NoError(t, model.DB.Model(&model.WorkspaceFundingMigration{}).Count(&receipts).Error)
	require.Zero(t, receipts)
	require.NoError(t, model.DB.First(team, team.ID).Error)
	require.Zero(t, team.FundingVersion)
	require.NoError(t, model.DB.Model(&second).Update("end_time", common.GetTimestamp()-1).Error)
	for range 2 {
		require.NoError(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{first.Id}, 0))
	}
	require.NoError(t, model.DB.First(first, first.Id).Error)
	require.Equal(t, team.ID, first.WorkspaceTeamID)
	require.NoError(t, model.DB.First(&second, second.Id).Error)
	require.Equal(t, team.ID, second.WorkspaceTeamID)
	require.Less(t, second.EndTime, common.GetTimestamp())
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
}

func TestWorkspaceTeamSubscriptionPolicyAndSettlement(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	require.False(t, sub.AllowWalletOverflow)
	require.NoError(t, model.EnableWorkspaceSubscriptionWalletOverflow())
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.False(t, sub.AllowWalletOverflow, "startup must not enable team PAYGO")
	personal, err := model.SetWorkspaceSubscription(owner.Id, 400, 100)
	require.NoError(t, err)
	require.True(t, personal.AllowWalletOverflow)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("quota", 9000).Error)
	// Historical flags are ignored for new team reservations and settlement.
	require.NoError(t, model.DB.Model(sub).Update("allow_wallet_overflow", true).Error)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "team-settlement", 80, "reserve")
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "team-refund", 20, "reserve")
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "team-settlement", 81, "reserve")
	require.Error(t, err, "extending a reservation cannot borrow legacy wallet")
	_, err = model.LeaveWorkspaceTeam(member.Id, team.ID)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(sub).Update("end_time", common.GetTimestamp()-1).Error)
	_, err = model.LeaveWorkspaceTeam(owner.Id, team.ID)
	require.NoError(t, err)
	for range 2 {
		settled, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "team-settlement", 120, "settle")
		require.NoError(t, err)
		require.Zero(t, settled.Record.WalletPreConsumed)
		_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "team-refund", 0, "refund")
		require.NoError(t, err)
	}
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 120, sub.AmountUsed)
	require.EqualValues(t, 120, sub.WeeklyUsed)
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.EqualValues(t, 9000, account.Quota)
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	require.Zero(t, personal.AmountUsed)
}

func TestWorkspaceTeamSubscriptionWeekAndMigration(t *testing.T) {
	owner, _, team, key := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "prior-week", 30, "reserve")
	require.NoError(t, err)
	now := common.GetTimestamp()
	boundary := now - 2*model.BillingWeekSeconds - 1
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"start_time": now - 3*model.BillingWeekSeconds, "weekly_reset_at": boundary}).Error)
	require.NoError(t, model.DB.Model(&model.SubscriptionPreConsumeRecord{}).Where("request_id = ?", "prior-week").Update("weekly_reset_at", boundary).Error)
	require.Equal(t, 200, fundingRequest(t, key, "current-week", 100, 100).Code)
	require.Equal(t, 403, fundingRequest(t, key, "no-carryover", 1, 1).Code)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "prior-week", 0, "refund")
	require.NoError(t, err)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 100, sub.AmountUsed)
	require.EqualValues(t, 100, sub.WeeklyUsed, "prior-week refund never replenishes current-week cap")
	require.Equal(t, now+model.BillingWeekSeconds-1, sub.WeeklyResetAt, "quiet weeks advance to exactly one current window")
	// The last partial week of a paid contract retains its proportional cap.
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"purchase_price_cents": 9900, "weekly_used": 0, "weekly_reset_at": now + model.BillingWeekSeconds, "end_time": now + model.BillingWeekSeconds/2}).Error)
	require.Equal(t, 200, fundingRequest(t, key, "last-half-week", 50, 50).Code)
	require.Equal(t, 403, fundingRequest(t, key, "last-half-week-exhausted", 1, 1).Code)
	// Migration remains fail-closed and may transfer subscriptions only.
	require.NoError(t, model.DB.Delete(&model.WorkspaceTeamAccount{}, team.ID).Error)
	require.NoError(t, model.DB.Model(team).Update("funding_version", 0).Error)
	personal, err := model.SetWorkspaceSubscription(owner.Id, 400, 100)
	require.NoError(t, err)
	require.Error(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{personal.Id}, 1))
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
}

func TestWorkspaceTeamLegacyFundingReconciliation(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	for _, withSubscription := range []bool{false, true} {
		t.Run(fmt.Sprintf("subscription=%v", withSubscription), func(t *testing.T) {
			var subscriptionID int
			var reserved int64
			if withSubscription {
				subscriptionID, reserved = sub.Id, 20
				require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"amount_used": 20, "weekly_used": 20, "status": "expired", "end_time": common.GetTimestamp() - 1}).Error)
			}
			record := model.SubscriptionPreConsumeRecord{RequestId: fmt.Sprintf("legacy-funding-%v", withSubscription), UserId: owner.Id, WorkspaceTeamID: team.ID, UserSubscriptionId: subscriptionID, FundingMode: "subscription_first", Status: "consumed", PreConsumed: reserved, WalletPreConsumed: 10, WeeklyResetAt: common.GetTimestamp() + model.BillingWeekSeconds}
			require.NoError(t, model.DB.Create(&record).Error)
			require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Updates(map[string]any{"quota": 90, "closed_at": common.GetTimestamp()}).Error)
			for range 2 {
				state, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, record.RequestId, reserved+15, "settle")
				require.NoError(t, err)
				if withSubscription {
					require.EqualValues(t, 25, state.Record.PreConsumed)
					require.EqualValues(t, 10, state.Record.WalletPreConsumed)
				} else {
					require.Zero(t, state.Record.PreConsumed)
					require.EqualValues(t, 15, state.Record.WalletPreConsumed)
				}
			}
			for range 2 {
				_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, record.RequestId, 0, "reconcile")
				require.NoError(t, err)
			}
			var account model.WorkspaceTeamAccount
			require.NoError(t, model.DB.First(&account, team.ID).Error)
			require.EqualValues(t, 100, account.Quota)
			require.NoError(t, model.DB.First(&record, record.Id).Error)
			require.Equal(t, "refunded", record.Status)
			require.NoError(t, model.DB.First(&owner, owner.Id).Error)
			require.Equal(t, 1000, owner.Quota)
		})
	}
}

func TestWorkspaceWeeklyAllowanceAPI(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&owner).Update("role", common.RoleCommonUser).Error)
	admin := model.User{Username: "weekly-editor", AffCode: "weekly-editor", Role: common.RoleRootUser, Status: 1, AuthVersion: 1, Group: "default"}
	require.NoError(t, model.DB.Create(&admin).Error)
	session, err := service.CreateLoginSession(admin.Id, "password", "127.0.0.1", "weekly-editor")
	require.NoError(t, err)
	r := gin.New()
	r.PUT("/users/:id/allowance", middleware.AdminAuth(), WorkspaceSessionRequired, AdminSetWorkspaceSubscription)
	call := func(body string, success bool) string {
		t.Helper()
		req := httptest.NewRequest("PUT", fmt.Sprintf("/users/%d/allowance", owner.Id), strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+session.AccessToken)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		require.Contains(t, res.Body.String(), fmt.Sprintf(`"success":%v`, success), res.Body.String())
		return res.Body.String()
	}
	for _, scope := range []int{0, team.ID} {
		call(fmt.Sprintf(`{"team_id":%d,"weekly_usd":0.002}`, scope), true)
		var sub model.UserSubscription
		require.NoError(t, model.DB.Where("user_id = ? AND workspace_team_id = ?", owner.Id, scope).First(&sub).Error)
		require.EqualValues(t, 1000, sub.WeeklyAmount)
		require.EqualValues(t, 4000, sub.AmountTotal)
		require.Equal(t, model.BillingPeriodSeconds, sub.EndTime-sub.StartTime)
		require.Equal(t, scope == 0, sub.AllowWalletOverflow)
		// Preserve a historical non-four-week ratio and all usage/date fields.
		require.NoError(t, model.DB.Model(&sub).Updates(map[string]any{"amount_total": 4300, "amount_used": 250, "weekly_used": 250}).Error)
		require.NoError(t, model.DB.First(&sub, sub.Id).Error)
		before := sub
		call(fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"weekly_usd":0.004}`, sub.Id, scope), true)
		require.NoError(t, model.DB.First(&sub, sub.Id).Error)
		before.AmountTotal, before.WeeklyAmount, before.UpdatedAt = 8600, 2000, sub.UpdatedAt
		require.Equal(t, before, sub)
		for _, value := range []string{"0", "-1", "1e100"} {
			message := call(fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"weekly_usd":%s}`, sub.Id, scope, value), false)
			require.NotContains(t, message, "月")
			require.Contains(t, message, "周限额")
		}
		call(fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"monthly_usd":0,"weekly_usd":1}`, sub.Id, scope), false)
		call(fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"monthly_usd":1,"weekly_usd":2}`, sub.Id, scope), false)
		call(fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"monthly_usd":0.008,"weekly_usd":0.004}`, sub.Id, scope), true)
		require.NoError(t, model.DB.First(&sub, sub.Id).Error)
		require.EqualValues(t, 4000, sub.AmountTotal, "explicit monthly input retains legacy semantics")
		require.NoError(t, model.DB.Model(&sub).Update("weekly_amount", 0).Error)
		call(fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"weekly_usd":0.002}`, sub.Id, scope), true)
		require.NoError(t, model.DB.First(&sub, sub.Id).Error)
		require.EqualValues(t, 4000, sub.AmountTotal, "adding a week cap preserves an unbounded contract total")
	}
}
