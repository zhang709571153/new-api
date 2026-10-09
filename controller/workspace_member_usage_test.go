package controller

import (
	"fmt"
	"net/http"
	"strings"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/service/authz"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func weeklyMemberUsage(t *testing.T, team *model.WorkspaceTeam, userID int) (int64, int64) {
	t.Helper()
	_, _, subscriptions, err := model.WorkspaceTeamFunding(team)
	require.NoError(t, err)
	usage, resetAt, err := model.GetWorkspaceMemberWeeklyUsage(team.ID, subscriptions)
	require.NoError(t, err)
	return usage[userID], resetAt
}

func TestWorkspaceMemberWeeklyOpeningView(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	cap := 10
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	require.Equal(t, 200, fundingRequest(t, key, "opening-view", 7, 7).Code)
	require.NoError(t, model.DB.Model(&model.WorkspaceMemberWeeklyUsage{}).Where("team_id = ? AND user_id = ? AND subscription_id = ?", team.ID, member.Id, sub.Id).Update("opening_used_quota", 30).Error)
	view, err := service.GetWorkspaceTeamView(owner.Id, 0)
	require.NoError(t, err)
	for _, row := range view.Members {
		if row.UserID == member.Id {
			require.Equal(t, float64(37)/common.QuotaPerUnit, row.WeeklyUsedUSD, "historical opening must appear without spending it again")
			require.Equal(t, float64(30)/common.QuotaPerUnit, row.OpeningUsedUSD)
			require.Equal(t, float64(10)/common.QuotaPerUnit, row.AllowanceUSD)
			require.Equal(t, float64(40)/common.QuotaPerUnit, row.EffectiveWeeklyAllowanceUSD)
			require.Equal(t, float64(3)/common.QuotaPerUnit, row.BalanceUSD)
		}
	}
	summary, err := service.GetWorkspaceSummaryForScope(member.Id, "team")
	require.NoError(t, err)
	require.Equal(t, float64(3)/common.QuotaPerUnit, summary.BalanceUSD)
	require.Equal(t, 200, fundingRequest(t, key, "opening-remaining", 3, 3).Code)
	require.Equal(t, 403, fundingRequest(t, key, "opening-cap-blocked", 1, 1).Code)
}

func openingImportFixture(t *testing.T, team *model.WorkspaceTeam, sub *model.UserSubscription) model.WorkspaceMemberOpeningImport {
	t.Helper()
	_, err := model.InitializeWorkspaceMemberWeeklyQuotas()
	require.NoError(t, err)
	require.NoError(t, model.DB.AutoMigrate(&model.WorkspaceMemberOpeningReceipt{}))
	var members []model.WorkspaceMember
	require.NoError(t, model.DB.Where("team_id = ?", team.ID).Order("user_id").Find(&members).Error)
	window := model.WorkspaceMemberOpeningWindow{TeamID: team.ID, OwnerUserID: team.OwnerUserID, SubscriptionID: sub.Id, WeeklyResetAt: sub.WeeklyResetAt}
	for _, member := range members {
		window.Members = append(window.Members, model.WorkspaceMemberOpeningEntry{UserID: member.UserID, TokenID: member.TokenID, OpeningUsedQuota: 30})
	}
	return model.WorkspaceMemberOpeningImport{OperationID: "opening-test", SourceSHA256: strings.Repeat("a", 64), Windows: []model.WorkspaceMemberOpeningWindow{window}}
}

func TestWorkspaceMemberWeeklyOpeningImport(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	cap := 20
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	input := openingImportFixture(t, team, sub)
	for id, amount := range map[string]int64{"opening-settle": 6, "opening-refund": 1} {
		_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, id, amount, "reserve", key.Id)
		require.NoError(t, err)
	}
	beforeView, err := service.GetWorkspaceTeamView(owner.Id, 0)
	require.NoError(t, err)
	var beforeToken model.Token
	require.NoError(t, model.DB.First(&beforeToken, key.Id).Error)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	beforeSub := *sub
	// A parent maintenance guard failure must roll back opening rows AND receipt.
	applied, err := model.BackfillWorkspaceMemberWeeklyOpening(input, func() error { return fmt.Errorf("test maintenance guard lost") })
	require.ErrorContains(t, err, "guard lost")
	require.False(t, applied)
	var receipts int64
	require.NoError(t, model.DB.Model(&model.WorkspaceMemberOpeningReceipt{}).Count(&receipts).Error)
	require.Zero(t, receipts)
	var opening int64
	require.NoError(t, model.DB.Model(&model.WorkspaceMemberWeeklyUsage{}).Select("COALESCE(SUM(opening_used_quota),0)").Scan(&opening).Error)
	require.Zero(t, opening)
	applied, err = model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.NoError(t, err)
	require.True(t, applied)
	applied, err = model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.NoError(t, err)
	require.False(t, applied)
	var afterToken model.Token
	require.NoError(t, model.DB.First(&afterToken, key.Id).Error)
	require.Equal(t, beforeToken, afterToken, "import must not write any token field or key")
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Equal(t, beforeSub, *sub, "team counters and contract remain independent")
	afterView, err := service.GetWorkspaceTeamView(owner.Id, 0)
	require.NoError(t, err)
	for i, row := range afterView.Members {
		require.Equal(t, beforeView.Members[i].BalanceUSD, row.BalanceUSD)
		require.Equal(t, beforeView.Members[i].AllowanceUSD, row.AllowanceUSD)
		require.Equal(t, beforeView.Members[i].UsedUSD, row.UsedUSD)
		if row.IsOwner {
			require.Equal(t, row.BalanceUSD, row.EffectiveWeeklyAllowanceUSD)
		}
	}
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "opening-settle", 8, "settle", key.Id)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "opening-refund", 0, "refund", key.Id)
	require.NoError(t, err)
	used, _ := weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 8, used)
	applied, err = model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.NoError(t, err)
	require.False(t, applied, "replay must not re-import post-release spending")
	_, err = model.UpdateWorkspaceTeamSubscriptionWeeklyUsed(owner.Id, team.ID, sub.Id, 0, sub.WeeklyResetAt)
	require.NoError(t, err)
	used, _ = weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 8, used, "team used adjustment does not alter member history")
	cap = 15
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	summary, err := service.GetWorkspaceSummaryForScope(member.Id, "team")
	require.NoError(t, err)
	require.Equal(t, float64(7)/common.QuotaPerUnit, summary.BalanceUSD)
	// The next window has neither imported history nor extra spending budget.
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "opening-late", 1, "reserve", key.Id)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(sub).Update("weekly_reset_at", common.GetTimestamp()-1).Error)
	used, newReset := weeklyMemberUsage(t, team, member.Id)
	require.Zero(t, used)
	require.NotEqual(t, input.Windows[0].WeeklyResetAt, newReset)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "opening-late", 0, "refund", key.Id)
	require.NoError(t, err)
	afterView, err = service.GetWorkspaceTeamView(owner.Id, 0)
	require.NoError(t, err)
	for _, row := range afterView.Members {
		require.Zero(t, row.OpeningUsedUSD)
		require.Zero(t, row.WeeklyUsedUSD)
		if row.UserID == member.Id {
			require.Equal(t, float64(15)/common.QuotaPerUnit, row.EffectiveWeeklyAllowanceUSD)
		}
	}
	applied, err = model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.NoError(t, err)
	require.False(t, applied, "completed operation remains a no-op after rollover")
}

func TestWorkspaceMemberWeeklyOpeningGuards(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	for _, change := range []func(*model.WorkspaceMemberOpeningImport){
		func(v *model.WorkspaceMemberOpeningImport) { v.Windows[0].WeeklyResetAt-- },
		func(v *model.WorkspaceMemberOpeningImport) { v.Windows[0].SubscriptionID++ },
		func(v *model.WorkspaceMemberOpeningImport) { v.Windows[0].OwnerUserID++ },
		func(v *model.WorkspaceMemberOpeningImport) { v.Windows[0].Members[0].TokenID++ },
		func(v *model.WorkspaceMemberOpeningImport) { v.Windows[0].Members = v.Windows[0].Members[1:] },
		func(v *model.WorkspaceMemberOpeningImport) { v.Windows[0].Members[0].OpeningUsedQuota = -1 },
		func(v *model.WorkspaceMemberOpeningImport) {
			v.Windows[0].Members[1].OpeningUsedQuota = common.MaxWalletQuota
		},
	} {
		input := openingImportFixture(t, team, sub)
		change(&input)
		applied, err := model.BackfillWorkspaceMemberWeeklyOpening(input)
		require.Error(t, err)
		require.False(t, applied)
		var rows int64
		require.NoError(t, model.DB.Model(&model.WorkspaceMemberWeeklyUsage{}).Count(&rows).Error)
		require.Zero(t, rows, "a later member failure must roll back earlier members")
	}
	input := openingImportFixture(t, team, sub)
	originalReset := sub.WeeklyResetAt
	expiredWindow := common.GetTimestamp() - 1
	require.NoError(t, model.DB.Model(sub).Update("weekly_reset_at", expiredWindow).Error)
	input.Windows[0].WeeklyResetAt = expiredWindow
	_, err = model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.ErrorContains(t, err, "window changed or elapsed")
	require.NoError(t, model.DB.Model(sub).Update("weekly_reset_at", originalReset).Error)
	input.Windows[0].WeeklyResetAt = originalReset
	applied, err := model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.NoError(t, err)
	require.True(t, applied)
	input.SourceSHA256 = strings.Repeat("b", 64)
	_, err = model.BackfillWorkspaceMemberWeeklyOpening(input)
	require.ErrorContains(t, err, "receipt input mismatch")
}

func TestWorkspaceMemberWeeklyOpeningSchemaAndConcurrency(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	require.Equal(t, 200, fundingRequest(t, key, "opening-upgrade", 7, 7).Code)
	// Upgrade the exact rc8 table shape, preserving data and composite identity.
	require.NoError(t, model.DB.Migrator().DropColumn(&model.WorkspaceMemberWeeklyUsage{}, "opening_used_quota"))
	for range 2 {
		require.NoError(t, model.DB.AutoMigrate(&model.WorkspaceMemberWeeklyUsage{}, &model.WorkspaceMemberOpeningReceipt{}))
	}
	used, _ := weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 7, used)
	input := openingImportFixture(t, team, sub)
	sql, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		sql.SetMaxOpenConns(8)
		defer sql.SetMaxOpenConns(1)
	}
	start := make(chan struct{})
	results := make(chan error, 3)
	var wg sync.WaitGroup
	for range 2 {
		wg.Go(func() { <-start; _, err := model.BackfillWorkspaceMemberWeeklyOpening(input); results <- err })
	}
	wg.Go(func() {
		<-start
		_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "opening-concurrent", 3, "reserve", key.Id)
		results <- err
	})
	close(start)
	wg.Wait()
	close(results)
	for err := range results {
		require.NoError(t, err)
	}
	used, _ = weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 10, used)
	var receipts int64
	require.NoError(t, model.DB.Model(&model.WorkspaceMemberOpeningReceipt{}).Count(&receipts).Error)
	require.EqualValues(t, 1, receipts)
	var row model.WorkspaceMemberWeeklyUsage
	require.NoError(t, model.DB.First(&row, "team_id = ? AND user_id = ?", team.ID, member.Id).Error)
	require.EqualValues(t, 30, row.OpeningUsedQuota)
	require.Error(t, model.DB.Create(&row).Error, "upgrade preserves composite primary-key uniqueness")
	for range 2 {
		require.NoError(t, model.DB.AutoMigrate(&model.WorkspaceMemberWeeklyUsage{}, &model.WorkspaceMemberOpeningReceipt{}))
	}
	require.NoError(t, model.DB.First(&row, "team_id = ? AND user_id = ?", team.ID, member.Id).Error)
	require.EqualValues(t, 30, row.OpeningUsedQuota)
}

func TestWorkspaceMemberWeeklyUsageIndependence(t *testing.T) {
	owner, member, team, memberKey := setupFundingIsolation(t)
	ownerKey, err := model.GetWorkspaceKey(owner.Id, false)
	require.NoError(t, err)
	third := model.User{Username: "third-weekly", AffCode: "third-weekly", Group: "default", Status: common.UserStatusEnabled, AuthVersion: 1}
	require.NoError(t, model.DB.Create(&third).Error)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(third.Id, invite))
	cap := 15
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, third.Id, &cap, nil, nil))
	thirdKey, err := model.GetWorkspaceKey(third.Id, false)
	require.NoError(t, err)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	history := model.Log{UserId: owner.Id, TokenId: memberKey.Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, Quota: 17, PromptTokens: 23, CompletionTokens: 29}
	require.NoError(t, model.LOG_DB.Create(&history).Error)
	for i, entry := range []struct {
		key    *model.Token
		amount int
	}{{ownerKey, 5}, {memberKey, 10}, {thirdKey, 5}} {
		require.Equal(t, 200, fundingRequest(t, entry.key, fmt.Sprintf("independent-%d", i), entry.amount, entry.amount).Code)
	}
	_, err = model.UpdateWorkspaceTeamSubscriptionWeeklyUsed(owner.Id, team.ID, sub.Id, 0, sub.WeeklyResetAt)
	require.NoError(t, err)
	for id, expected := range map[int]int64{owner.Id: 5, member.Id: 10, third.Id: 5} {
		used, _ := weeklyMemberUsage(t, team, id)
		require.Equal(t, expected, used)
	}
	require.Equal(t, 200, fundingRequest(t, thirdKey, "after-team-reset", 5, 5).Code)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 5, sub.WeeklyUsed)
	used, resetAt := weeklyMemberUsage(t, team, third.Id)
	require.EqualValues(t, 10, used)
	view, err := service.GetWorkspaceTeamView(owner.Id, 0)
	require.NoError(t, err)
	for _, row := range view.Members {
		if row.UserID == third.Id {
			require.Equal(t, float64(10)/common.QuotaPerUnit, row.WeeklyUsedUSD)
			require.Equal(t, resetAt, row.WeeklyResetAt)
			require.Equal(t, float64(5)/common.QuotaPerUnit, row.BalanceUSD)
		}
	}
	var saved model.Log
	require.NoError(t, model.LOG_DB.First(&saved, history.Id).Error)
	require.Equal(t, history, saved, "weekly reset must retain lifetime cost and tokens")
	var token model.Token
	require.NoError(t, model.DB.First(&token, thirdKey.Id).Error)
	require.Equal(t, thirdKey.Key, token.Key)
	require.Equal(t, 10, token.UsedQuota)
}

func TestWorkspaceMemberWeeklyUsageRollover(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	cap := 10
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	for request, amount := range map[string]int64{"late-settle": 6, "late-refund": 1} {
		_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, request, amount, "reserve", key.Id)
		require.NoError(t, err)
	}
	require.Equal(t, 200, fundingRequest(t, key, "week-old-final", 3, 3).Code)
	require.Equal(t, 403, fundingRequest(t, key, "week-old-blocked", 1, 1).Code)
	_, originalResetAt := weeklyMemberUsage(t, team, member.Id)
	require.NoError(t, model.DB.Model(sub).Update("weekly_reset_at", common.GetTimestamp()-3600).Error)
	used, resetAt := weeklyMemberUsage(t, team, member.Id)
	require.Zero(t, used)
	require.NotEqual(t, originalResetAt, resetAt)
	require.Equal(t, 200, fundingRequest(t, key, "week-new-final", 10, 10).Code, "old exhausted token must not block a refreshed weekly cap")
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "late-settle", 8, "settle", key.Id)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "late-refund", 0, "refund", key.Id)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "late-refund", 0, "refund", key.Id)
	require.NoError(t, err)
	used, _ = weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 10, used, "late old-week settlement/refund cannot alter the new week")
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 10, sub.WeeklyUsed)
	var oldWindow model.WorkspaceMemberWeeklyUsage
	require.NoError(t, model.DB.First(&oldWindow, "team_id = ? AND user_id = ? AND weekly_reset_at = ?", team.ID, member.Id, originalResetAt).Error)
	require.EqualValues(t, 11, oldWindow.UsedQuota)
	var token model.Token
	require.NoError(t, model.DB.First(&token, key.Id).Error)
	require.Equal(t, 21, token.UsedQuota)
	require.Equal(t, key.Key, token.Key)
	status := common.UserStatusDisabled
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, nil, &status, nil))
	require.Equal(t, 401, fundingRequest(t, key, "paused-member", 1, 1).Code)
	status = common.UserStatusEnabled
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, nil, &status, nil))
	require.Equal(t, 403, fundingRequest(t, key, "new-week-cap-blocked", 1, 1).Code)
	require.NoError(t, model.RemoveWorkspaceMember(owner.Id, team.ID, member.Id))
	require.Equal(t, 401, fundingRequest(t, key, "removed-member", 1, 1).Code)
}

func TestWorkspaceMemberWeeklyUsageRollbackAndBootstrap(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&model.WorkspaceMember{}).Where("user_id = ?", member.Id).Update("weekly_quota", nil).Error)
	require.NoError(t, model.DB.Model(key).Updates(map[string]any{"remain_quota": 17, "used_quota": 90}).Error)
	count, err := model.InitializeWorkspaceMemberWeeklyQuotas()
	require.NoError(t, err)
	require.Positive(t, count)
	count, err = model.InitializeWorkspaceMemberWeeklyQuotas()
	require.NoError(t, err)
	require.Zero(t, count)
	membership, _, err := model.FindWorkspaceMembership(member.Id)
	require.NoError(t, err)
	require.NotNil(t, membership.WeeklyQuota)
	require.EqualValues(t, 17, *membership.WeeklyQuota)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	callback := "test:member_weekly_rollback"
	require.NoError(t, model.DB.Callback().Create().Before("gorm:create").Register(callback, func(tx *gorm.DB) {
		if _, ok := tx.Statement.Dest.(*model.WorkspaceMemberWeeklyUsage); ok {
			tx.AddError(fmt.Errorf("fixture member counter write failed"))
		}
	}))
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "counter-rollback", 5, "reserve", key.Id)
	require.NoError(t, model.DB.Callback().Create().Remove(callback))
	require.ErrorContains(t, err, "fixture member counter write failed")
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Zero(t, sub.WeeklyUsed)
	require.Zero(t, sub.AmountUsed)
	var token model.Token
	require.NoError(t, model.DB.First(&token, key.Id).Error)
	require.Equal(t, key.Key, token.Key)
	require.Equal(t, 17, token.RemainQuota)
	require.Equal(t, 90, token.UsedQuota)
	var records int64
	require.NoError(t, model.DB.Model(&model.SubscriptionPreConsumeRecord{}).Where("request_id = ?", "counter-rollback").Count(&records).Error)
	require.Zero(t, records)
	require.Equal(t, 200, fundingRequest(t, key, "counter-retry", 5, 5).Code)
	count, err = model.InitializeWorkspaceMemberWeeklyQuotas()
	require.NoError(t, err)
	require.Zero(t, count)
	membership, _, err = model.FindWorkspaceMembership(member.Id)
	require.NoError(t, err)
	require.EqualValues(t, 17, *membership.WeeklyQuota)
	// An old team entitlement with no weekly window cannot bypass member caps.
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"weekly_amount": 0, "weekly_reset_at": 0}).Error)
	require.Equal(t, 403, fundingRequest(t, key, "missing-weekly-window", 1, 1).Code)
}

func TestWorkspaceAdminTeamProvision(t *testing.T) {
	owner, member, oldTeam, oldKey := setupFundingIsolation(t)
	admin := model.User{Username: "provision-admin", AffCode: "provision-admin", Role: common.RoleRootUser, Status: common.UserStatusEnabled, AuthVersion: 1}
	require.NoError(t, model.DB.Create(&admin).Error)
	r := gin.New()
	r.POST("/api/admin/:id/team-provision", middleware.AdminAuth(), WorkspaceSessionRequired, middleware.RequirePermission(authz.TeamWrite), AdminProvisionWorkspaceTeam)
	path := fmt.Sprintf("/api/admin/%d/team-provision", member.Id)
	w, result := workspaceTeamPolicyCall(t, r, member.Id, path, `{"weekly_usd":10}`)
	require.Equal(t, http.StatusForbidden, w.Code)
	restricted := model.User{Username: "provision-restricted", AffCode: "provision-restricted", Role: common.RoleAdminUser, Status: common.UserStatusEnabled, AuthVersion: 1}
	require.NoError(t, model.DB.Create(&restricted).Error)
	require.NoError(t, authz.SetUserPermissions(restricted.Id, authz.PermissionsMap{authz.ResourceTeam: {authz.ActionWrite: false}}))
	w, _ = workspaceTeamPolicyCall(t, r, restricted.Id, path, `{"weekly_usd":10}`)
	require.Equal(t, http.StatusForbidden, w.Code)
	personal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	callback := "test:admin_provision_rollback"
	require.NoError(t, model.DB.Callback().Create().Before("gorm:create").Register(callback, func(tx *gorm.DB) {
		if _, ok := tx.Statement.Dest.(*model.UserSubscription); ok {
			tx.AddError(fmt.Errorf("fixture subscription grant failed"))
		}
	}))
	_, err = model.AdminProvisionWorkspaceTeam(member.Id, "Test team", 100)
	require.NoError(t, model.DB.Callback().Create().Remove(callback))
	require.ErrorContains(t, err, "fixture subscription grant failed")
	membership, team, err := model.FindWorkspaceMembership(member.Id)
	require.NoError(t, err)
	require.Equal(t, oldTeam.ID, team.ID)
	require.Equal(t, oldKey.Id, membership.TokenID)
	_, result = workspaceTeamPolicyCall(t, r, admin.Id, path, `{"weekly_usd":10}`)
	require.Equal(t, true, result["success"])
	data := result["data"].(map[string]any)
	teamID := int(data["team"].(map[string]any)["id"].(float64))
	require.NotEqual(t, oldTeam.ID, teamID)
	sub := data["subscription"].(map[string]any)
	require.Equal(t, float64(10)*common.QuotaPerUnit, sub["weekly_amount"])
	require.Equal(t, float64(teamID), sub["workspace_team_id"])
	require.Equal(t, "workspace_admin", sub["source"])
	require.Equal(t, false, sub["allow_wallet_overflow"])
	_, result = workspaceTeamPolicyCall(t, r, admin.Id, path, `{"weekly_usd":20}`)
	require.Equal(t, false, result["success"])
	require.Contains(t, result["message"], "编辑现有团队")
	var savedUser model.User
	require.NoError(t, model.DB.First(&savedUser, member.Id).Error)
	require.Equal(t, member.Quota, savedUser.Quota)
	preserved, err := model.GetWorkspaceKeyForScope(member.Id, "personal", false)
	require.NoError(t, err)
	require.Equal(t, personal.Key, preserved.Key)
	var oldOwner model.User
	require.NoError(t, model.DB.First(&oldOwner, owner.Id).Error)
	require.Equal(t, owner.Quota, oldOwner.Quota)
}

func TestWorkspaceMemberWeeklyUsageConcurrent(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	cap := 10
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	sqlDB, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		sqlDB.SetMaxOpenConns(12)
	}
	type outcome struct {
		request string
		err     error
	}
	results := make(chan outcome, 40)
	var group sync.WaitGroup
	for i := range 40 {
		group.Go(func() {
			request := fmt.Sprintf("member-concurrent-%d", i)
			_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, request, 1, "reserve", key.Id)
			results <- outcome{request, err}
		})
	}
	group.Wait()
	close(results)
	var accepted []string
	for result := range results {
		if result.err == nil {
			accepted = append(accepted, result.request)
		} else {
			require.Contains(t, result.err.Error(), "member weekly allowance exhausted")
		}
	}
	require.Len(t, accepted, 10)
	used, _ := weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 10, used)
	// Move the member while accepted requests finish against their old team.
	// Provisioning locks lifecycle rows; settlement locks the old funding account.
	start := make(chan struct{})
	errors := make(chan error, len(accepted)+1)
	for _, request := range accepted {
		group.Go(func() {
			<-start
			_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, request, 1, "settle", key.Id)
			errors <- err
		})
	}
	group.Go(func() {
		<-start
		_, err := model.AdminProvisionWorkspaceTeam(member.Id, "Concurrent grant", 50)
		errors <- err
	})
	close(start)
	group.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			require.False(t, strings.Contains(strings.ToLower(err.Error()), "deadlock"), err)
		}
		require.NoError(t, err)
	}
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 10, sub.WeeklyUsed)
	used, _ = weeklyMemberUsage(t, team, member.Id)
	require.EqualValues(t, 10, used)
	_, current, err := model.FindWorkspaceMembership(member.Id)
	require.NoError(t, err)
	require.NotEqual(t, team.ID, current.ID)
	require.Equal(t, member.Id, current.OwnerUserID)
}

func TestWorkspaceMemberWeeklyUsageTokenExpiry(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	for index, tc := range []struct {
		name    string
		status  int
		expires int64
	}{
		{"expired status", common.TokenStatusExpired, -1},
		{"expired clock", common.TokenStatusEnabled, common.GetTimestamp() - 1},
		{"disabled", common.TokenStatusDisabled, -1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			require.NoError(t, model.DB.Model(key).Updates(map[string]any{"status": common.TokenStatusEnabled, "expired_time": -1}).Error)
			settleID, refundID := fmt.Sprintf("expiry-settle-%d", index), fmt.Sprintf("expiry-refund-%d", index)
			_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, settleID, 3, "reserve", key.Id)
			require.NoError(t, err)
			_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, refundID, 2, "reserve", key.Id)
			require.NoError(t, err)
			require.NoError(t, model.DB.Model(key).Updates(map[string]any{"status": tc.status, "expired_time": tc.expires}).Error)
			// These calls represent an already authenticated request reaching the
			// funding transaction after its key changes state or expires.
			_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, fmt.Sprintf("expiry-new-%d", index), 1, "reserve", key.Id)
			require.ErrorIs(t, err, model.ErrWorkspaceAccess)
			_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, settleID, 4, "reserve", key.Id)
			require.ErrorIs(t, err, model.ErrWorkspaceAccess)
			_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, settleID, 4, "settle", key.Id)
			require.NoError(t, err)
			_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, refundID, 0, "refund", key.Id)
			require.NoError(t, err)
			require.NoError(t, model.DB.First(sub, sub.Id).Error)
			require.EqualValues(t, (index+1)*4, sub.WeeklyUsed)
			used, _ := weeklyMemberUsage(t, team, member.Id)
			require.Equal(t, sub.WeeklyUsed, used)
		})
	}
}
