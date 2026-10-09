package controller

import (
	"context"
	"fmt"
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestWorkspaceFundingAdminAllowanceScope(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&owner).Update("role", common.RoleCommonUser).Error)
	// Exercise the management path after the actual legacy-to-team migration.
	require.NoError(t, model.DB.Delete(&model.WorkspaceTeamAccount{}, team.ID).Error)
	require.NoError(t, model.DB.Model(team).Update("funding_version", 0).Error)
	teamSub, err := model.SetWorkspaceSubscription(owner.Id, 100000000, 25000000)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(teamSub).Updates(map[string]any{"amount_used": 41700, "weekly_used": 41700}).Error)
	require.NoError(t, model.DB.First(teamSub, teamSub.Id).Error)
	before := *teamSub
	require.NoError(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{teamSub.Id}, 0))
	admin := model.User{Username: "funding-admin", AffCode: "funding-admin", Group: "default", Role: common.RoleRootUser, Status: 1, AuthVersion: 1}
	require.NoError(t, model.DB.Create(&admin).Error)
	session, err := service.CreateLoginSession(admin.Id, "password", "127.0.0.1", "admin-funding-test")
	require.NoError(t, err)
	memberSession, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "member-funding-test")
	require.NoError(t, err)
	r := gin.New()
	a := r.Group("/admin", middleware.AdminAuth())
	a.GET("/users/:id/subscriptions", AdminListUserSubscriptions)
	a.PUT("/users/:id/allowance", WorkspaceSessionRequired, AdminSetWorkspaceSubscription)
	a.POST("/subscriptions/:id/invalidate", WorkspaceSessionRequired, AdminInvalidateUserSubscription)
	call := func(method, path, body, credential string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+credential)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	listURL := fmt.Sprintf("/admin/users/%d/subscriptions", owner.Id)
	writeURL := fmt.Sprintf("/admin/users/%d/allowance", owner.Id)
	res := call("GET", listURL, "", session.AccessToken)
	var listing struct {
		Success bool
		Data    []model.SubscriptionSummary
		Team    *model.WorkspaceTeam
	}
	require.NoError(t, common.Unmarshal(res.Body.Bytes(), &listing))
	require.True(t, listing.Success)
	require.Len(t, listing.Data, 1)
	require.Equal(t, team.ID, listing.Data[0].Subscription.WorkspaceTeamID)
	require.NotNil(t, listing.Team)
	require.Equal(t, team.ID, listing.Team.ID)
	require.Equal(t, "no-store", res.Header().Get("Cache-Control"))
	for _, body := range []string{`{"monthly_usd":200,"weekly_usd":40}`, `{"team_id":null,"monthly_usd":200,"weekly_usd":40}`, `{"team_id":-1,"monthly_usd":200,"weekly_usd":40}`, `{"team_id":999999,"monthly_usd":200,"weekly_usd":40}`} {
		require.Contains(t, call("PUT", writeURL, body, session.AccessToken).Body.String(), `"success":false`)
	}
	personal, err := model.GetAllUserSubscriptions(owner.Id)
	require.NoError(t, err)
	require.Empty(t, personal, "old cached form must not silently recreate personal credit")
	teamBody := fmt.Sprintf(`{"team_id":%d,"monthly_usd":200,"weekly_usd":40}`, team.ID)
	require.Contains(t, call("PUT", writeURL, teamBody, session.AccessToken).Body.String(), `"success":true`)
	require.NoError(t, model.DB.First(teamSub, teamSub.Id).Error)
	require.EqualValues(t, 20000000, teamSub.WeeklyAmount)
	require.Equal(t, before.AmountUsed, teamSub.AmountUsed)
	require.Equal(t, before.WeeklyUsed, teamSub.WeeklyUsed)
	require.Equal(t, before.StartTime, teamSub.StartTime)
	require.Equal(t, before.EndTime, teamSub.EndTime)
	require.Contains(t, call("PUT", writeURL, `{"team_id":0,"monthly_usd":300,"weekly_usd":60}`, session.AccessToken).Body.String(), `"success":true`)
	personal, err = model.GetAllUserSubscriptions(owner.Id)
	require.NoError(t, err)
	require.Len(t, personal, 1)
	require.Zero(t, personal[0].Subscription.WorkspaceTeamID)
	personalBefore := *personal[0].Subscription
	require.EqualValues(t, 150000000, personalBefore.AmountTotal)
	require.Equal(t, 403, call("GET", listURL, "", memberSession.AccessToken).Code)
	require.Equal(t, 403, call("PUT", writeURL, teamBody, memberSession.AccessToken).Code)
	res = call("GET", fmt.Sprintf("/admin/users/%d/subscriptions", member.Id), "", session.AccessToken)
	require.Contains(t, res.Body.String(), `"team":null`, "member management must not imply ownership of the team pool")
	res = call("POST", fmt.Sprintf("/admin/subscriptions/%d/invalidate", teamSub.Id), `{}`, session.AccessToken)
	require.Contains(t, res.Body.String(), `"success":true`)
	require.NoError(t, model.DB.First(teamSub, teamSub.Id).Error)
	require.Equal(t, "cancelled", teamSub.Status)
	require.Equal(t, before.AmountUsed, teamSub.AmountUsed)
	// Re-activation creates a new team entitlement; the old ledger stays intact.
	require.Contains(t, call("PUT", writeURL, teamBody, session.AccessToken).Body.String(), `"success":true`)
	all, err := model.GetUserSubscriptionsForAdmin(owner.Id)
	require.NoError(t, err)
	require.Len(t, all, 3)
	var activeTeams int
	for _, item := range all {
		if item.Subscription.WorkspaceTeamID == team.ID && item.Subscription.Status == "active" {
			activeTeams++
			require.NotEqual(t, teamSub.Id, item.Subscription.Id)
			require.Zero(t, item.Subscription.AmountUsed)
		}
	}
	require.Equal(t, 1, activeTeams)
	personal, err = model.GetAllUserSubscriptions(owner.Id)
	require.NoError(t, err)
	require.Equal(t, personalBefore, *personal[0].Subscription)
}

func TestWorkspaceSubscriptionTokenFunding(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	owner := model.User{Username: "funding-owner", Group: "default", Status: 1, Quota: 5000, AffCode: "funding-owner"}
	member := model.User{Username: "funding-member", Group: "default", Status: 1, Quota: 5000, AffCode: "funding-member"}
	require.NoError(t, model.DB.Create(&owner).Error)
	require.NoError(t, model.DB.Create(&member).Error)
	team, err := model.CreateWorkspaceTeam(owner.Id, "Funding test")
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("quota", 5000).Error)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(member.Id, invite))
	allowance := 1000
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &allowance, nil, nil))
	token, err := model.GetWorkspaceKey(member.Id, false)
	require.NoError(t, err)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 200, 50)
	require.NoError(t, err)
	router := gin.New()
	router.Use(middleware.RequestId())
	router.POST("/v1/responses", middleware.TokenAuth(), func(c *gin.Context) {
		info := relaycommon.GenRelayInfoResponses(c, &dto.OpenAIResponsesRequest{})
		session, apiErr := service.NewBillingSession(c, info, 10)
		if apiErr != nil {
			c.Status(apiErr.StatusCode)
			return
		}
		require.NoError(t, session.Settle(12))
		require.Equal(t, owner.Id, info.UserId)
		require.Equal(t, sub.Id, info.SubscriptionId, "team token must fund from owner's active subscription")
		c.Status(200)
	})
	call := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/v1/responses", strings.NewReader(`{}`))
		req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(token))
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		return res
	}
	require.Equal(t, 200, call().Code)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 12, sub.AmountUsed)
	require.EqualValues(t, 12, sub.WeeklyUsed)
	_, err = model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 200, 10)
	require.NoError(t, err)
	require.Equal(t, 403, call().Code, "exhausted team allowance cannot use a legacy wallet balance")
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 5000, owner.Quota, "personal wallet remains untouched")
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.EqualValues(t, 5000, account.Quota)
}

func TestWorkspaceAdminSubscriptionWalletContinuation(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	user := model.User{Username: "admin-continuation", AffCode: "admin-continuation", Status: 1, Group: "default", Quota: 100}
	require.NoError(t, model.DB.Create(&user).Error)
	sub, err := model.SetWorkspaceSubscription(user.Id, 200, 50)
	require.NoError(t, err)
	require.True(t, sub.AllowWalletOverflow)
	// Emulate an existing grant and an in-flight legacy reservation at upgrade.
	require.NoError(t, model.DB.Model(sub).Update("allow_wallet_overflow", false).Error)
	_, err = model.PreConsumeUserSubscription("legacy-before-migration", user.Id, "gpt-6-sol", 0, 40)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	before := *sub
	for range 2 {
		require.NoError(t, model.EnableWorkspaceSubscriptionWalletOverflow())
	}
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.True(t, sub.AllowWalletOverflow)
	require.Equal(t, before.AmountUsed, sub.AmountUsed)
	require.Equal(t, before.WeeklyUsed, sub.WeeklyUsed)
	require.Equal(t, before.StartTime, sub.StartTime)
	require.Equal(t, before.EndTime, sub.EndTime)
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(sub.Id, 0, "legacy-before-migration"))
	call := func(id string, estimate, actual int, allowed bool) {
		t.Helper()
		ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
		info := &relaycommon.RelayInfo{UserId: user.Id, RequestId: id, IsPlayground: true, ForcePreConsume: true}
		info.UserSetting.BillingPreference = "wallet_only"
		session, apiErr := service.NewBillingSession(ctx, info, estimate)
		if !allowed {
			require.NotNil(t, apiErr)
			return
		}
		require.Nil(t, apiErr)
		require.NoError(t, session.Settle(actual))
		require.NoError(t, session.Settle(actual))
	}
	call("admin-split", 20, 20, true)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 50, sub.AmountUsed)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.Equal(t, 90, user.Quota, "consume the final 10 allowance before 10 cash")
	call("admin-wallet", 20, 20, true)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.Equal(t, 70, user.Quota)
	call("admin-empty", 71, 71, false)
	// A new week restores allowance priority without resetting total usage.
	require.NoError(t, model.DB.Model(sub).Update("weekly_reset_at", common.GetTimestamp()-1).Error)
	call("admin-new-week", 20, 20, true)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 70, sub.AmountUsed)
	require.EqualValues(t, 20, sub.WeeklyUsed)
	_, err = model.UpdateSubscriptionFirstFunding(user.Id, "admin-refund", 40, "reserve")
	require.NoError(t, err)
	_, err = model.UpdateSubscriptionFirstFunding(user.Id, "admin-refund", 0, "refund")
	require.NoError(t, err)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.Equal(t, 70, user.Quota)
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"amount_used": 200}).Error)
	call("admin-total-exhausted", 10, 10, true)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 210, sub.AmountUsed, "weekly contracts keep cumulative usage without a hidden total cap")
	require.EqualValues(t, 30, sub.WeeklyUsed)
	require.NoError(t, model.DB.Model(sub).Update("end_time", common.GetTimestamp()-1).Error)
	call("admin-expired", 10, 10, true)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.Equal(t, 60, user.Quota)
}

func TestWorkspaceSubscriptionBudgets(t *testing.T) {
	setupTeamDatabase(t)
	user := model.User{Username: "budget-owner", Group: "default", Role: common.RoleCommonUser, Status: common.UserStatusEnabled, AuthVersion: 1, AffCode: "budget-owner"}
	require.NoError(t, model.DB.Create(&user).Error)
	sub, err := model.SetWorkspaceSubscription(user.Id, 200, 50)
	require.NoError(t, err)
	require.EqualValues(t, 0, sub.AmountUsed)
	consume := func(id string, amount int64) error {
		_, err := model.PreConsumeUserSubscription(id, user.Id, "gpt-6-sol", 0, amount)
		return err
	}
	require.NoError(t, consume("first", 40))
	require.Error(t, model.ReserveUserSubscriptionDelta(sub.Id, 11, "first"))
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(sub.Id, -10, "first"))
	require.Error(t, consume("weekly-denied", 21))
	require.NoError(t, consume("failed", 20))
	require.NoError(t, model.RefundSubscriptionPreConsume("failed"))
	require.NoError(t, model.RefundSubscriptionPreConsume("failed"))
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 30, sub.AmountUsed)
	require.EqualValues(t, 30, sub.WeeklyUsed)
	require.NoError(t, consume("old-week", 10))
	boundary := common.GetTimestamp() - 1
	require.NoError(t, model.DB.Model(sub).Update("weekly_reset_at", boundary).Error)
	require.NoError(t, model.DB.Model(&model.SubscriptionPreConsumeRecord{}).Where("request_id = ?", "old-week").Update("weekly_reset_at", boundary).Error)
	require.NoError(t, consume("new-week", 40))
	require.NoError(t, model.RefundSubscriptionPreConsume("old-week"))
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 70, sub.AmountUsed)
	require.EqualValues(t, 40, sub.WeeklyUsed, "old-week refund must not refill this week")
	updated, err := model.SetWorkspaceSubscription(user.Id, 60, 50)
	require.NoError(t, err)
	require.Equal(t, sub.Id, updated.Id)
	require.Equal(t, sub.EndTime, updated.EndTime)
	require.EqualValues(t, 70, updated.AmountUsed)
	require.NoError(t, consume("period-total-does-not-block", 1))
	_, err = model.SetWorkspaceSubscription(user.Id, 20, 30)
	require.Error(t, err)
	// A member can inspect allowance, but cannot grant themselves service credit.
	session, err := service.CreateLoginSession(user.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	router := gin.New()
	router.PUT("/allowance/:id", middleware.AdminAuth(), WorkspaceSessionRequired, AdminSetWorkspaceSubscription)
	request := httptest.NewRequest("PUT", fmt.Sprintf("/allowance/%d", user.Id), strings.NewReader(`{"monthly_usd":200,"weekly_usd":50}`))
	request.Header.Set("Authorization", "Bearer "+session.AccessToken)
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)
	require.Equal(t, 403, recorder.Code)
	// Concurrent requests reserve the same budget under a database row lock.
	other := model.User{Username: "budget-concurrent", Group: "default", Status: 1, AffCode: "budget-concurrent"}
	require.NoError(t, model.DB.Create(&other).Error)
	_, err = model.SetWorkspaceSubscription(other.Id, 200, 50)
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		db, _ := model.DB.DB()
		db.SetMaxOpenConns(8)
	}
	var accepted atomic.Int32
	var wg sync.WaitGroup
	for i := range 8 {
		wg.Go(func() {
			if _, err := model.PreConsumeUserSubscription(fmt.Sprintf("parallel-%d", i), other.Id, "gpt-6-sol", 0, 10); err == nil {
				accepted.Add(1)
			}
		})
	}
	wg.Wait()
	require.EqualValues(t, 5, accepted.Load())
	// Actual output can exceed its estimate; never drop the bill or silently
	// make that already-completed upstream work free.
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(sub.Id, 1, "first"))
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 72, sub.AmountUsed)
	require.NoError(t, consume("week-final", sub.WeeklyLimit()-sub.WeeklyUsed))
	require.Error(t, consume("after-actual-overage", 1))
}

func TestWorkspacePaidSubscriptionMixedFunding(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	for index, tc := range []struct {
		name                string
		pre, actual, wallet int
		weekly, total       int64
		overflow            bool
		wantSub             int64
		wantWallet          int
		deny                bool
	}{
		{"split_boundary", 200, 200, 500, 100, 400, true, 100, 400, false},
		{"overestimate_refunds_cash_first", 200, 50, 500, 100, 400, true, 50, 500, false},
		{"underestimate_spills_to_cash", 50, 200, 500, 100, 400, true, 100, 400, false},
		{"zero_estimate", 0, 200, 500, 100, 400, true, 100, 400, false},
		{"cash_debt_does_not_consume_allowance", 50, 50, -50, 100, 400, true, 50, -50, false},
		{"period_total_is_not_weekly_cap", 200, 200, 500, 400, 100, true, 200, 500, false},
		{"combined_insufficient", 200, 200, 50, 100, 400, true, 0, 50, true},
		{"overflow_disabled", 200, 200, 500, 100, 400, false, 0, 500, true},
		{"completed_overage_is_audited_debt", 50, 300, 50, 100, 400, true, 100, -150, false},
		{"strict_completed_overage_stays_subscription", 50, 200, 500, 100, 400, false, 200, 500, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			user := model.User{Username: fmt.Sprintf("mixed-%d", index), AffCode: fmt.Sprintf("mixed-%d", index), Status: 1, Group: "default", Quota: tc.wallet}
			require.NoError(t, model.DB.Create(&user).Error)
			now := common.GetTimestamp()
			sub := model.UserSubscription{UserId: user.Id, Source: "cny_order", PurchasePriceCents: 9900, PurchaseTitle: "Lite", Status: "active", StartTime: now, EndTime: now + model.BillingPeriodSeconds, WeeklyAmount: tc.weekly, AmountTotal: tc.total, WeeklyResetAt: now + model.BillingWeekSeconds, AllowWalletOverflow: tc.overflow}
			require.NoError(t, model.DB.Create(&sub).Error)
			ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
			info := &relaycommon.RelayInfo{UserId: user.Id, RequestId: tc.name, IsPlayground: true, ForcePreConsume: true}
			info.UserSetting.BillingPreference = "wallet_only"
			session, apiErr := service.NewBillingSession(ctx, info, tc.pre)
			if tc.deny {
				require.NotNil(t, apiErr)
			} else {
				require.Nil(t, apiErr)
				require.Equal(t, "subscription_first", info.UserSetting.BillingPreference)
				require.NoError(t, session.Settle(tc.actual))
				require.NoError(t, session.Settle(tc.actual), "repeat settlement is idempotent")
				require.Equal(t, tc.wantSub, info.SubscriptionPreConsumed+info.SubscriptionPostDelta)
				require.EqualValues(t, tc.wallet-tc.wantWallet, info.WalletQuotaDeducted)
			}
			require.NoError(t, model.DB.First(&user, user.Id).Error)
			require.NoError(t, model.DB.First(&sub, sub.Id).Error)
			require.Equal(t, tc.wantWallet, user.Quota)
			require.Equal(t, tc.wantSub, sub.AmountUsed)
			require.Equal(t, tc.wantSub, sub.WeeklyUsed)
		})
	}
}

func TestWorkspacePaidSubscriptionMixedLifecycle(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	user := model.User{Username: "mixed-life", AffCode: "mixed-life", Status: 1, Group: "default", Quota: 500}
	require.NoError(t, model.DB.Create(&user).Error)
	now := common.GetTimestamp()
	sub := model.UserSubscription{UserId: user.Id, Source: "cny_order", PurchasePriceCents: 9900, Status: "active", StartTime: now, EndTime: now + model.BillingPeriodSeconds, WeeklyAmount: 100, AmountTotal: 400, WeeklyResetAt: now + model.BillingWeekSeconds, AllowWalletOverflow: true}
	require.NoError(t, model.DB.Create(&sub).Error)
	_, err := model.UpdateSubscriptionFirstFunding(user.Id, "mixed-old-week", 200, "reserve")
	require.NoError(t, err)
	// A retry/image quantity increase reserves only its difference. Reducing it
	// restores permanent cash first and does not create a second reservation.
	_, err = model.UpdateSubscriptionFirstFunding(user.Id, "mixed-old-week", 250, "reserve")
	require.NoError(t, err)
	_, err = model.UpdateSubscriptionFirstFunding(user.Id, "mixed-old-week", 200, "reserve")
	require.NoError(t, err)
	boundary := now - 1
	require.NoError(t, model.DB.Model(&sub).Update("weekly_reset_at", boundary).Error)
	require.NoError(t, model.DB.Model(&model.SubscriptionPreConsumeRecord{}).Where("request_id = ?", "mixed-old-week").Update("weekly_reset_at", boundary).Error)
	_, err = model.UpdateSubscriptionFirstFunding(user.Id, "mixed-new-week", 50, "reserve")
	require.NoError(t, err)
	lateReserve, err := model.UpdateSubscriptionFirstFunding(user.Id, "mixed-old-week", 250, "reserve")
	require.NoError(t, err)
	require.EqualValues(t, 100, lateReserve.Record.PreConsumed, "old request cannot consume the new week's allowance")
	require.EqualValues(t, 150, lateReserve.Record.WalletPreConsumed)
	require.NoError(t, model.RefundSubscriptionPreConsume("mixed-old-week"))
	require.NoError(t, model.RefundSubscriptionPreConsume("mixed-old-week"))
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.EqualValues(t, 50, sub.AmountUsed)
	require.EqualValues(t, 50, sub.WeeklyUsed)
	require.Equal(t, 500, user.Quota)
	// Asynchronous task/legacy post-consume reconciliation uses the durable
	// split, not an all-subscription delta after the in-memory session is gone.
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(sub.Id, 100, "mixed-new-week"))
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.EqualValues(t, 100, sub.AmountUsed)
	require.EqualValues(t, 100, sub.WeeklyUsed)
	require.Equal(t, 450, user.Quota)
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(sub.Id, -125, "mixed-new-week"))
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.EqualValues(t, 25, sub.AmountUsed)
	require.EqualValues(t, 25, sub.WeeklyUsed)
	require.Equal(t, 500, user.Quota)
	// Expiry returns to permanent balance without touching the expired plan.
	require.NoError(t, model.DB.Model(&sub).Update("end_time", now-1).Error)
	ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
	info := &relaycommon.RelayInfo{UserId: user.Id, RequestId: "mixed-expired", IsPlayground: true, ForcePreConsume: true}
	session, apiErr := service.NewBillingSession(ctx, info, 40)
	require.Nil(t, apiErr)
	require.NoError(t, session.Settle(40))
	require.Equal(t, "wallet", info.BillingSource)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.Equal(t, 460, user.Quota)
}

func TestWorkspacePaidSubscriptionMixedConcurrent(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	user := model.User{Username: "mixed-concurrent", AffCode: "mixed-concurrent", Status: 1, Quota: 100}
	require.NoError(t, model.DB.Create(&user).Error)
	now := common.GetTimestamp()
	sub := model.UserSubscription{UserId: user.Id, Source: "cny_order", PurchasePriceCents: 9900, Status: "active", StartTime: now, EndTime: now + model.BillingPeriodSeconds, WeeklyAmount: 50, AmountTotal: 200, WeeklyResetAt: now + model.BillingWeekSeconds, AllowWalletOverflow: true}
	require.NoError(t, model.DB.Create(&sub).Error)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		db, err := model.DB.DB()
		require.NoError(t, err)
		db.SetMaxOpenConns(8)
	}
	var accepted atomic.Int32
	var wg sync.WaitGroup
	for i := range 8 {
		wg.Go(func() {
			if _, err := model.UpdateSubscriptionFirstFunding(user.Id, fmt.Sprintf("mixed-parallel-%d", i), 30, "reserve"); err == nil {
				accepted.Add(1)
			}
		})
	}
	wg.Wait()
	require.EqualValues(t, 5, accepted.Load())
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	require.EqualValues(t, 50, sub.AmountUsed)
	require.EqualValues(t, 50, sub.WeeklyUsed)
	require.Zero(t, user.Quota)
}

func TestWorkspacePaidSubscriptionMixedTeamAndTask(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	owner := model.User{Username: "mixed-owner", AffCode: "mixed-owner", Status: 1, Group: "default", Quota: 500}
	member := model.User{Username: "mixed-member", AffCode: "mixed-member", Status: 1, Group: "default", Quota: 700}
	require.NoError(t, model.DB.Create(&owner).Error)
	require.NoError(t, model.DB.Create(&member).Error)
	_, err := legacyWorkspaceTeamFixture(owner.Id, "Mixed funding")
	require.NoError(t, err)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(member.Id, invite))
	allowance := 1000
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &allowance, nil, nil))
	token, err := model.GetWorkspaceKey(member.Id, false)
	require.NoError(t, err)
	now := common.GetTimestamp()
	sub := model.UserSubscription{UserId: owner.Id, Source: "cny_order", PurchasePriceCents: 9900, Status: "active", StartTime: now, EndTime: now + model.BillingPeriodSeconds, WeeklyAmount: 100, AmountTotal: 400, WeeklyResetAt: now + model.BillingWeekSeconds, AllowWalletOverflow: true}
	require.NoError(t, model.DB.Create(&sub).Error)
	router := gin.New()
	router.POST("/v1/responses", middleware.TokenAuth(), func(c *gin.Context) {
		info := relaycommon.GenRelayInfoResponses(c, &dto.OpenAIResponsesRequest{})
		info.RequestId = "mixed-team-request"
		session, apiErr := service.NewBillingSession(c, info, 200)
		require.Nil(t, apiErr)
		require.Equal(t, owner.Id, info.UserId)
		require.NoError(t, session.Settle(150))
		require.EqualValues(t, 100, info.SubscriptionPreConsumed+info.SubscriptionPostDelta)
		require.EqualValues(t, 50, info.WalletQuotaDeducted)
		c.Status(200)
	})
	req := httptest.NewRequest("POST", "/v1/responses", strings.NewReader(`{}`))
	req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(token))
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	require.Equal(t, 200, res.Code)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	require.Equal(t, 450, owner.Quota)
	require.Equal(t, 700, member.Quota, "team member's personal PAYGO is separate")
	// Mixed allocation records survive the old seven-day cache cleanup, or a
	// delayed task refund would lose its permanent-cash portion.
	require.NoError(t, model.DB.Model(&model.SubscriptionPreConsumeRecord{}).Where("request_id = ?", "mixed-team-request").UpdateColumn("updated_at", now-8*24*3600).Error)
	_, err = model.CleanupSubscriptionPreConsumeRecords(7 * 24 * 3600)
	require.NoError(t, err)
	retained, err := model.SubscriptionFirstFundingRecord("mixed-team-request")
	require.NoError(t, err)
	require.NotNil(t, retained)
	// Paid upgrade does not redirect an in-flight settlement/refund into the
	// newly issued subscription, and retrying an absolute task result is safe.
	require.NoError(t, model.DB.Model(&sub).Update("status", "upgraded").Error)
	newSub := model.UserSubscription{UserId: owner.Id, Source: "cny_order", PurchasePriceCents: 19900, Status: "active", StartTime: now, EndTime: now + model.BillingPeriodSeconds, WeeklyAmount: 200, AmountTotal: 800, WeeklyResetAt: now + model.BillingWeekSeconds, AllowWalletOverflow: true}
	require.NoError(t, model.DB.Create(&newSub).Error)
	require.NoError(t, model.DB.AutoMigrate(&model.Task{}, &model.Channel{}))
	require.NoError(t, model.DB.Model(&owner).Update("used_quota", 150).Error)
	task := model.Task{TaskID: "mixed-persisted-task", UserId: owner.Id, Quota: 150, PrivateData: model.TaskPrivateData{BillingSource: "subscription", SubscriptionId: sub.Id, TokenId: token.Id, Execution: &model.TaskExecutionSnapshot{RequestID: "mixed-team-request"}}}
	require.NoError(t, model.DB.Create(&task).Error)
	service.RecalculateTaskQuota(context.Background(), &task, 50, "mixed funding review")
	require.Equal(t, 50, task.Quota)
	var adjustmentLog model.Log
	require.NoError(t, model.LOG_DB.Where("user_id = ? AND content = ?", owner.Id, "mixed funding review").First(&adjustmentLog).Error)
	var logDetails map[string]any
	require.NoError(t, common.UnmarshalJsonStr(adjustmentLog.Other, &logDetails))
	require.Equal(t, 100, adjustmentLog.Quota)
	require.EqualValues(t, 50, logDetails["subscription_consumed"])
	require.EqualValues(t, 50, logDetails["wallet_quota_deducted"])
	staleTask := task
	staleTask.Quota = 150
	service.RecalculateTaskQuota(context.Background(), &staleTask, 50, "replayed task result")
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.NoError(t, model.DB.First(&newSub, newSub.Id).Error)
	require.Equal(t, 500, owner.Quota)
	require.EqualValues(t, 50, sub.AmountUsed)
	require.Zero(t, newSub.AmountUsed)
	require.EqualValues(t, 50, owner.UsedQuota, "replayed task result does not repeat accounting")
	require.NoError(t, model.DB.First(token, token.Id).Error)
	require.Equal(t, 50, token.UsedQuota)
	require.True(t, service.RefundTaskQuota(context.Background(), &task, "failed after reconciliation"))
	require.True(t, service.RefundTaskQuota(context.Background(), &task, "duplicate refund"))
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.NoError(t, model.DB.First(&sub, sub.Id).Error)
	require.Equal(t, 500, owner.Quota)
	require.Zero(t, sub.AmountUsed)
	require.NoError(t, model.DB.First(token, token.Id).Error)
	require.Zero(t, token.UsedQuota)
}
