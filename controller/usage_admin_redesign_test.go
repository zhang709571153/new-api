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
	"github.com/QuantumNous/new-api/service/authz"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestWorkspaceSubscriptionExpiryEditing(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&owner).Update("role", common.RoleCommonUser).Error)
	personal, err := model.SetWorkspaceSubscription(owner.Id, 2000000, 500000)
	require.NoError(t, err)
	teamSub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 4000000, 1000000)
	require.NoError(t, err)
	_, err = model.PreConsumeUserSubscription("expiry-pending", owner.Id, "test", 0, 150000)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	before := *personal
	var reservation model.SubscriptionPreConsumeRecord
	require.NoError(t, model.DB.Where("request_id = ?", "expiry-pending").First(&reservation).Error)
	admin := model.User{Username: "expiry-admin", AffCode: "expiry-admin", Role: 100, Status: 1, AuthVersion: 1, Group: "default"}
	require.NoError(t, model.DB.Create(&admin).Error)
	session, err := service.CreateLoginSession(admin.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	userSession, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	r := gin.New()
	r.PUT("/users/:id/allowance", middleware.AdminAuth(), WorkspaceSessionRequired, AdminSetWorkspaceSubscription)
	call := func(id int, body, token string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("PUT", fmt.Sprintf("/users/%d/allowance", id), strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	newEnd := before.EndTime + model.BillingWeekSeconds
	body := fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"end_time":%d,"expected_end_time":%d}`, before.Id, newEnd, before.EndTime)
	require.Equal(t, 403, call(owner.Id, body, userSession.AccessToken).Code)
	require.Contains(t, call(member.Id, body, session.AccessToken).Body.String(), `"success":false`)
	require.Contains(t, call(owner.Id, body, session.AccessToken).Body.String(), `"success":true`)
	require.Contains(t, call(owner.Id, body, session.AccessToken).Body.String(), `"success":false`, "stale browser cannot replay an expiry update")
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	expected := before
	expected.EndTime = newEnd
	expected.UpdatedAt = personal.UpdatedAt
	require.Equal(t, expected, *personal, "usage, cadence, price, limits and funding policy are preserved")
	var afterReservation model.SubscriptionPreConsumeRecord
	require.NoError(t, model.DB.First(&afterReservation, reservation.Id).Error)
	require.Equal(t, reservation, afterReservation)
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(personal.Id, -50000, "expiry-pending"))
	for _, invalid := range []string{
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"end_time":%d}`, personal.Id, newEnd),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"end_time":%d,"expected_end_time":%d,"weekly_usd":100}`, personal.Id, newEnd, newEnd),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":%d,"end_time":%d,"expected_end_time":%d}`, personal.Id, team.ID, newEnd, newEnd),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"end_time":0,"expected_end_time":%d}`, personal.Id, newEnd),
		fmt.Sprintf(`{"subscription_id":%d,"team_id":0,"end_time":253402300800,"expected_end_time":%d}`, personal.Id, newEnd),
	} {
		require.Contains(t, call(owner.Id, invalid, session.AccessToken).Body.String(), `"success":false`)
	}
	shortEnd := common.GetTimestamp() + 86400
	_, err = model.UpdateWorkspaceSubscriptionExpiry(owner.Id, team.ID, teamSub.Id, shortEnd, teamSub.EndTime)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(teamSub, teamSub.Id).Error)
	require.Equal(t, shortEnd, teamSub.EndTime)
	require.NoError(t, model.DB.Model(personal).Update("end_time", common.GetTimestamp()-1).Error)
	_, err = model.UpdateWorkspaceSubscriptionExpiry(owner.Id, 0, personal.Id, newEnd, common.GetTimestamp()-1)
	require.Error(t, err, "expired subscription cannot be revived through expiry edit")
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
}

func TestAdminWorkspaceManagementAndLogFilters(t *testing.T) {
	owner, member, team, memberToken := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&owner).Update("role", common.RoleCommonUser).Error)
	admin := model.User{Username: "team-manager", AffCode: "team-manager", Role: 100, Status: 1, AuthVersion: 1, Group: "default"}
	require.NoError(t, model.DB.Create(&admin).Error)
	session, err := service.CreateLoginSession(admin.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	memberSession, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	r := gin.New()
	r.PATCH("/teams/:team_id/members/:id", middleware.AdminAuth(), middleware.RequirePermission(authz.TeamWrite), WorkspaceSessionRequired, AdminManageWorkspaceTeam)
	call := func(teamID, userID int, token string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("PATCH", fmt.Sprintf("/teams/%d/members/%d", teamID, userID), strings.NewReader(`{"allowance_usd":3,"status":2}`))
		req.Header.Set("Authorization", "Bearer "+token)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	require.Equal(t, 403, call(team.ID, member.Id, memberSession.AccessToken).Code)
	require.Contains(t, call(team.ID, member.Id, session.AccessToken).Body.String(), `"success":true`)
	require.Contains(t, call(team.ID+100, member.Id, session.AccessToken).Body.String(), `"success":false`)
	require.Contains(t, call(team.ID, owner.Id, session.AccessToken).Body.String(), `"success":false`)
	editor := model.User{Username: "limited-editor", AffCode: "limited-editor", Role: 10, Status: 1, AuthVersion: 1, Group: "default"}
	require.NoError(t, model.DB.Create(&editor).Error)
	editorSession, err := service.CreateLoginSession(editor.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	require.Contains(t, call(team.ID, member.Id, editorSession.AccessToken).Body.String(), `"success":true`)
	require.NoError(t, authz.SetUserPermissions(editor.Id, authz.PermissionsMap{authz.ResourceTeam: {authz.ActionWrite: false}}))
	require.Equal(t, 403, call(team.ID, member.Id, editorSession.AccessToken).Code, "revoking team.write also blocks API access")
	require.NoError(t, model.DB.First(memberToken, memberToken.Id).Error)
	require.Equal(t, common.TokenStatusDisabled, memberToken.Status)
	management := &model.WorkspaceTeamManagement{ActorID: admin.Id, TeamID: team.ID}
	require.NoError(t, model.RenameWorkspaceTeam(owner.Id, team.ID, "Renamed", management))
	require.Error(t, model.RenameWorkspaceTeam(member.Id, team.ID, "Intruder"))
	require.Error(t, model.RenameWorkspaceTeam(owner.Id, team.ID, ""))
	// Caller filtering includes retired credentials but excludes the owner's
	// personal requests and other members charged to the same funding account.
	personal, err := model.GetWorkspaceKeyForScope(owner.Id, "personal", true)
	require.NoError(t, err)
	teamKey, err := model.GetWorkspaceKey(owner.Id, false)
	require.NoError(t, err)
	now := common.GetTimestamp()
	logs := []model.Log{
		{UserId: owner.Id, TokenId: memberToken.Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, CreatedAt: now, Quota: 10, PromptTokens: 100},
		{UserId: owner.Id, TokenId: teamKey.Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, CreatedAt: now, Quota: 20, PromptTokens: 200},
		{UserId: owner.Id, TokenId: personal.Id, WorkspaceTeamID: -1, Type: model.LogTypeConsume, CreatedAt: now, Quota: 30, PromptTokens: 300},
	}
	require.NoError(t, model.LOG_DB.Create(&logs).Error)
	scope, err := model.ResolveAdminWorkspaceLogScope(member.Id, team.ID)
	require.NoError(t, err)
	var filtered []model.Log
	require.NoError(t, scope.Apply(model.LOG_DB.Model(&model.Log{})).Find(&filtered).Error)
	require.Len(t, filtered, 1)
	require.Equal(t, 10, filtered[0].Quota)
	require.NoError(t, model.DB.Delete(&member).Error)
	require.NoError(t, model.AdminRemoveWorkspaceMember(management, member.Id), "administrators can clean up a deleted member without erasing historical attribution")
	scope, err = model.ResolveAdminWorkspaceLogScope(member.Id, team.ID)
	require.NoError(t, err)
	require.NoError(t, scope.Apply(model.LOG_DB.Model(&model.Log{})).Find(&filtered).Error)
	require.Len(t, filtered, 1)
	scope, err = model.ResolveAdminWorkspaceLogScope(0, team.ID)
	require.NoError(t, err)
	require.NoError(t, scope.Apply(model.LOG_DB.Model(&model.Log{})).Find(&filtered).Error)
	require.Len(t, filtered, 2)
	require.NoError(t, model.DB.Model(&admin).Update("role", 10).Error)
	require.NoError(t, model.DB.Model(&owner).Update("role", 100).Error)
	require.Error(t, model.RenameWorkspaceTeam(owner.Id, team.ID, "Forbidden", management))
}

func TestCustomerUsageLogRangeBoundaries(t *testing.T) {
	setupTeamDatabase(t)
	user := model.User{Username: "range-customer", AffCode: "range-customer", Role: common.RoleCommonUser, Status: 1, Group: "default"}
	require.NoError(t, model.DB.Create(&user).Error)
	const start, end int64 = 1000, 1100
	logs := []model.Log{
		{UserId: user.Id, Username: user.Username, Type: model.LogTypeConsume, CreatedAt: start - 1, RequestId: "before", Quota: 1},
		{UserId: user.Id, Username: user.Username, Type: model.LogTypeConsume, CreatedAt: start, RequestId: "start", Quota: 10},
		{UserId: user.Id, Username: user.Username, Type: model.LogTypeConsume, CreatedAt: end - 1, RequestId: "last", Quota: 100},
		{UserId: user.Id, Username: user.Username, Type: model.LogTypeConsume, CreatedAt: end, RequestId: "after", Quota: 1000},
	}
	require.NoError(t, model.LOG_DB.Create(&logs).Error)
	summary, err := model.GetTeamOverview(start, end)
	require.NoError(t, err)
	require.EqualValues(t, 2, summary.Totals.Requests)
	require.EqualValues(t, 110, summary.Totals.Quota)
	// Customer links subtract one millisecond from the exclusive summary end.
	// Milliseconds are converted to Unix seconds by the log filters.
	items, total, err := model.GetAllLogs(model.LogTypeConsume, start, (end*1000-1)/1000, "", user.Username, "", 0, 20, 0, "", "", "")
	require.NoError(t, err)
	require.EqualValues(t, 2, total)
	require.ElementsMatch(t, []string{"start", "last"}, []string{items[0].RequestId, items[1].RequestId})
	require.Equal(t, 110, items[0].Quota+items[1].Quota)
}
