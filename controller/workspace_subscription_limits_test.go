package controller

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestWorkspaceSubscriptionLimitEditing(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&owner).Update("role", common.RoleCommonUser).Error)
	now := common.GetTimestamp()
	plan := model.SubscriptionPlan{Title: "Limit editing plan", DurationUnit: model.SubscriptionDurationMonth, DurationValue: 1, QuotaResetPeriod: model.SubscriptionResetNever}
	require.NoError(t, model.DB.Create(&plan).Error)
	personal := model.UserSubscription{PlanId: plan.Id, UserId: owner.Id, Source: "cny_order", PurchasePriceCents: 19900, PurchaseTitle: "Paid plan", Status: "active", StartTime: now - 60, EndTime: now + model.BillingPeriodSeconds, WeeklyAmount: 1000000, AmountTotal: 4000000, WeeklyResetAt: now + model.BillingWeekSeconds, AllowWalletOverflow: false}
	require.NoError(t, model.DB.Create(&personal).Error)
	teamSub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 8000000, 2000000)
	require.NoError(t, err)
	_, err = model.PreConsumeUserSubscription("limits-pending", owner.Id, "gpt-6-sol", 0, 600000)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(&personal, personal.Id).Error)
	before := personal
	var reservation model.SubscriptionPreConsumeRecord
	require.NoError(t, model.DB.Where("request_id = ?", "limits-pending").First(&reservation).Error)
	admin := model.User{Username: "limit-editor", AffCode: "limit-editor", Role: common.RoleRootUser, Status: 1, AuthVersion: 1, Group: "default"}
	require.NoError(t, model.DB.Create(&admin).Error)
	session, err := service.CreateLoginSession(admin.Id, "password", "127.0.0.1", "limit-editor")
	require.NoError(t, err)
	memberSession, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "limit-reader")
	require.NoError(t, err)
	r := gin.New()
	r.PUT("/users/:id/allowance", middleware.AdminAuth(), WorkspaceSessionRequired, AdminSetWorkspaceSubscription)
	call := func(userID int, body, token string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("PUT", fmt.Sprintf("/users/%d/allowance", userID), strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	body := fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"monthly_usd":4,"weekly_usd":1}`, personal.Id)
	// The list/GET may predate a migration. Even a creation (no subscription ID)
	// must check the original team snapshot inside the write transaction.
	for _, staleBody := range []string{
		fmt.Sprintf(`{"team_id":0,"monthly_usd":4,"weekly_usd":1,"funding_context":{"team_id":%d,"funding_version":0}}`, team.ID),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"monthly_usd":4,"weekly_usd":1,"funding_context":{"team_id":%d,"funding_version":0}}`, personal.Id, team.ID),
	} {
		require.Contains(t, call(owner.Id, staleBody, session.AccessToken).Body.String(), `"success":false`)
	}
	res := call(owner.Id, body, session.AccessToken)
	require.Contains(t, res.Body.String(), `"success":true`)
	require.Equal(t, "no-store", res.Header().Get("Cache-Control"))
	require.NoError(t, model.DB.First(&personal, personal.Id).Error)
	expected := before
	expected.AmountTotal, expected.WeeklyAmount, expected.UpdatedAt = 2000000, 500000, personal.UpdatedAt
	require.Equal(t, expected, personal, "only limits and update timestamp may change, including when below current usage")
	var afterReservation model.SubscriptionPreConsumeRecord
	require.NoError(t, model.DB.First(&afterReservation, reservation.Id).Error)
	require.Equal(t, reservation, afterReservation)
	_, err = model.PreConsumeUserSubscription("limits-exhausted", owner.Id, "gpt-6-sol", 0, 1)
	require.Error(t, err, "new requests respect the lowered cap")
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(personal.Id, -100000, "limits-pending"))
	require.NoError(t, model.DB.First(&personal, personal.Id).Error)
	require.EqualValues(t, 500000, personal.AmountUsed, "in-flight settlement still works")
	require.EqualValues(t, 500000, personal.WeeklyUsed)
	for _, invalid := range []string{
		fmt.Sprintf(`{"subscription_id":%d,"monthly_usd":4,"weekly_usd":1}`, personal.Id),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"monthly_usd":4,"weekly_usd":1}`, personal.Id, team.ID),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"monthly_usd":4,"weekly_usd":1}`, teamSub.Id),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"monthly_usd":1,"weekly_usd":2}`, personal.Id),
		`{"subscription_id":0,"team_id":0,"monthly_usd":4,"weekly_usd":1}`,
		`{"subscription_id":999999,"team_id":0,"monthly_usd":4,"weekly_usd":1}`,
	} {
		require.Contains(t, call(owner.Id, invalid, session.AccessToken).Body.String(), `"success":false`)
	}
	require.Contains(t, call(member.Id, body, session.AccessToken).Body.String(), `"success":false`)
	require.Equal(t, 403, call(owner.Id, body, memberSession.AccessToken).Code)
	teamBefore := *teamSub
	teamBody := fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"monthly_usd":20,"weekly_usd":5,"funding_context":{"team_id":%d,"funding_version":1}}`, teamSub.Id, team.ID, team.ID)
	require.Contains(t, call(owner.Id, teamBody, session.AccessToken).Body.String(), `"success":true`)
	require.NoError(t, model.DB.First(teamSub, teamSub.Id).Error)
	teamBefore.AmountTotal, teamBefore.WeeklyAmount, teamBefore.UpdatedAt = 10000000, 2500000, teamSub.UpdatedAt
	require.Equal(t, teamBefore, *teamSub)
	for _, changes := range []map[string]any{{"status": "cancelled"}, {"status": "active", "end_time": now - 1}, {"end_time": now + 1000, "start_time": now + 500}} {
		require.NoError(t, model.DB.Model(&personal).Updates(changes).Error)
		require.Contains(t, call(owner.Id, body, session.AccessToken).Body.String(), `"success":false`)
	}
	var count int64
	require.NoError(t, model.DB.Model(&model.UserSubscription{}).Count(&count).Error)
	require.EqualValues(t, 2, count, "invalid or stale edits never create a subscription")
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota, "editing limits never moves PAYGO funds")
	// A late weekly window is refreshed in the response, not persisted by edits.
	require.NoError(t, model.DB.Model(&personal).Updates(map[string]any{"start_time": now - model.BillingWeekSeconds, "end_time": now + 1000, "weekly_reset_at": now - 1, "weekly_used": 400000}).Error)
	updated, err := model.UpdateWorkspaceSubscriptionLimits(owner.Id, 0, personal.Id, 2000000, 700000)
	require.NoError(t, err)
	require.Zero(t, updated.WeeklyUsed)
	require.Greater(t, updated.WeeklyLimitAmount, int64(0))
	require.Less(t, updated.WeeklyLimitAmount, int64(700000), "paid final fractional week stays prorated")
	require.NoError(t, model.DB.First(&personal, personal.Id).Error)
	require.EqualValues(t, 400000, personal.WeeklyUsed)
	require.Equal(t, now-1, personal.WeeklyResetAt)
	// Unlimited-to-weekly starts tracking future usage; past total stays intact.
	require.NoError(t, model.DB.Model(&personal).Updates(map[string]any{"weekly_amount": 0, "weekly_used": 0, "weekly_reset_at": 0, "end_time": now + model.BillingPeriodSeconds}).Error)
	updated, err = model.UpdateWorkspaceSubscriptionLimits(owner.Id, 0, personal.Id, 2000000, 700000)
	require.NoError(t, err)
	require.Zero(t, updated.WeeklyUsed)
	require.EqualValues(t, 500000, updated.AmountUsed)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("closed_at", now).Error)
	require.Contains(t, call(owner.Id, teamBody, session.AccessToken).Body.String(), `"success":false`)
}
