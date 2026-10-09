package controller

import (
	"fmt"
	"net/http"
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
	"gorm.io/gorm"
)

func workspaceTeamPolicyRouter() *gin.Engine {
	r := gin.New()
	w := r.Group("/api/workspace", middleware.UserAuth(), WorkspaceSessionRequired)
	w.POST("/team", CreateWorkspaceTeam)
	w.POST("/team/join", JoinWorkspaceTeam)
	w.POST("/team/leave", LeaveWorkspaceTeam)
	return r
}

func workspaceTeamPolicyCall(t *testing.T, r *gin.Engine, userID int, path, body string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	session, err := service.CreateLoginSession(userID, "password", "127.0.0.1", "team-policy-test")
	require.NoError(t, err)
	req := httptest.NewRequest("POST", path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+session.AccessToken)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	var result map[string]any
	require.NoError(t, common.Unmarshal(w.Body.Bytes(), &result))
	return w, result
}

func TestWorkspaceTeamCreationPolicy(t *testing.T) {
	setupTeamDatabase(t)
	r := workspaceTeamPolicyRouter()
	for _, test := range []struct {
		name string
		role int
		deny bool
	}{
		{"ordinary", common.RoleCommonUser, false},
		{"administrator", common.RoleAdminUser, false},
		{"restricted-administrator", common.RoleAdminUser, true},
		{"root", common.RoleRootUser, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			user := model.User{Username: test.name, AffCode: test.name, Role: test.role, Status: common.UserStatusEnabled, AuthVersion: 1, Quota: 1234, Group: "default"}
			require.NoError(t, model.DB.Create(&user).Error)
			if test.deny {
				require.NoError(t, authz.SetUserPermissions(user.Id, authz.PermissionsMap{authz.ResourceTeam: {authz.ActionWrite: false}}))
			}
			w, result := workspaceTeamPolicyCall(t, r, user.Id, "/api/workspace/team", `{"name":"Provisioned team"}`)
			require.Equal(t, http.StatusForbidden, w.Code)
			require.Equal(t, false, result["success"])
			require.Equal(t, "请先购买团队套餐，购买成功后自动开通团队", result["message"])
			for _, table := range []any{&model.WorkspaceTeam{}, &model.WorkspaceMember{}, &model.WorkspacePersonalKey{}, &model.Token{}} {
				var count int64
				column := "user_id"
				if _, ok := table.(*model.WorkspaceTeam); ok {
					column = "owner_user_id"
				}
				require.NoError(t, model.DB.Model(table).Where(column+" = ?", user.Id).Count(&count).Error)
				require.Zero(t, count)
			}
			var saved model.User
			require.NoError(t, model.DB.First(&saved, user.Id).Error)
			require.Equal(t, user.Quota, saved.Quota)
		})
	}
}

func TestWorkspaceTeamProvisioningTransaction(t *testing.T) {
	owner, member, team, oldKey := setupFundingIsolation(t)
	personal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	for _, rollback := range []bool{true, false} {
		var created *model.WorkspaceTeam
		err = model.DB.Transaction(func(tx *gorm.DB) error {
			dissolved, keys, err := model.LeaveWorkspaceTeamTx(tx, member.Id, team.ID)
			if err != nil {
				return err
			}
			require.False(t, dissolved)
			require.Equal(t, []string{oldKey.Key}, keys)
			created, err = model.CreateWorkspaceTeamTx(tx, member.Id, "Paid team fixture")
			if err != nil {
				return err
			}
			if rollback {
				return fmt.Errorf("fixture payment rollback")
			}
			return nil
		})
		var key model.Token
		require.NoError(t, model.DB.First(&key, oldKey.Id).Error)
		membership, current, findErr := model.FindWorkspaceMembership(member.Id)
		require.NoError(t, findErr)
		if rollback {
			require.ErrorContains(t, err, "fixture payment rollback")
			require.Equal(t, team.ID, current.ID)
			require.Equal(t, oldKey.Id, membership.TokenID)
			require.Equal(t, common.TokenStatusEnabled, key.Status)
		} else {
			require.NoError(t, err)
			require.Equal(t, created.ID, current.ID)
			require.Equal(t, member.Id, current.OwnerUserID)
			require.Equal(t, common.TokenStatusDisabled, key.Status)
		}
		personalAfter, err := model.GetWorkspaceKeyForScope(member.Id, "personal", false)
		require.NoError(t, err)
		require.Equal(t, personal.Key, personalAfter.Key)
		var oldTeam model.WorkspaceTeam
		require.NoError(t, model.DB.First(&oldTeam, team.ID).Error)
		require.Equal(t, owner.Id, oldTeam.OwnerUserID)
		var savedOwner model.User
		require.NoError(t, model.DB.First(&savedOwner, owner.Id).Error)
		require.Equal(t, owner.Quota, savedOwner.Quota)
	}
}

func TestWorkspaceOwnerCannotJoinAnotherTeam(t *testing.T) {
	setupTeamDatabase(t)
	owners := []model.User{
		{Username: "owner-a", AffCode: "owner-a", Status: common.UserStatusEnabled, Role: common.RoleCommonUser, AuthVersion: 1, Group: "default"},
		{Username: "owner-b", AffCode: "owner-b", Status: common.UserStatusEnabled, Role: common.RoleCommonUser, AuthVersion: 1, Group: "default"},
	}
	for i := range owners {
		require.NoError(t, model.DB.Create(&owners[i]).Error)
	}
	first, err := model.CreateWorkspaceTeam(owners[0].Id, "Existing team")
	require.NoError(t, err)
	second, err := model.CreateWorkspaceTeam(owners[1].Id, "Destination team")
	require.NoError(t, err)
	invite, _, err := model.CreateWorkspaceInvite(owners[1].Id)
	require.NoError(t, err)
	ownerKey, err := model.GetWorkspaceKey(owners[0].Id, false)
	require.NoError(t, err)
	r := workspaceTeamPolicyRouter()
	_, result := workspaceTeamPolicyCall(t, r, owners[0].Id, "/api/workspace/team/join", fmt.Sprintf(`{"code":%q}`, invite))
	require.Equal(t, false, result["success"])
	require.Equal(t, model.ErrWorkspaceOwnerJoin.Error(), result["message"])
	member, team, err := model.FindWorkspaceMembership(owners[0].Id)
	require.NoError(t, err)
	require.Equal(t, first.ID, team.ID)
	require.Equal(t, ownerKey.Id, member.TokenID)
	// Ownership is authoritative even if a legacy membership row is missing.
	require.NoError(t, model.DB.Delete(member).Error)
	require.ErrorIs(t, model.JoinWorkspaceTeam(owners[0].Id, invite), model.ErrWorkspaceOwnerJoin)
	for _, id := range []int{first.ID, second.ID} {
		var saved model.WorkspaceTeam
		require.NoError(t, model.DB.First(&saved, id).Error)
	}
	var savedKey model.Token
	require.NoError(t, model.DB.First(&savedKey, ownerKey.Id).Error)
	require.Equal(t, ownerKey.Key, savedKey.Key)
	require.Equal(t, common.TokenStatusEnabled, savedKey.Status)
}

func TestWorkspaceMemberCanChangeTeamsAtomic(t *testing.T) {
	owner, member, oldTeam, oldKey := setupFundingIsolation(t)
	personal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	destinationOwner := model.User{Username: "destination-owner", AffCode: "destination-owner", Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1}
	require.NoError(t, model.DB.Create(&destinationOwner).Error)
	destination, err := model.CreateWorkspaceTeam(destinationOwner.Id, "Destination team")
	require.NoError(t, err)
	revokedInvite, _, err := model.CreateWorkspaceInvite(destinationOwner.Id)
	require.NoError(t, err)
	invite, _, err := model.GetWorkspaceInvite(destinationOwner.Id, true)
	require.NoError(t, err)
	history := model.Log{UserId: owner.Id, TokenId: oldKey.Id, WorkspaceTeamID: oldTeam.ID, Type: model.LogTypeConsume, Quota: 7}
	require.NoError(t, model.LOG_DB.Create(&history).Error)
	var oldAccount model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&oldAccount, "team_id = ?", oldTeam.ID).Error)
	assertUnchanged := func() {
		t.Helper()
		membership, current, err := model.FindWorkspaceMembership(member.Id)
		require.NoError(t, err)
		require.Equal(t, oldTeam.ID, current.ID)
		require.Equal(t, oldKey.Id, membership.TokenID)
		var key model.Token
		require.NoError(t, model.DB.First(&key, oldKey.Id).Error)
		require.Equal(t, common.TokenStatusEnabled, key.Status)
		require.Equal(t, oldKey.RemainQuota, key.RemainQuota)
	}
	for _, code := range []string{"invalid", revokedInvite} {
		require.ErrorIs(t, model.JoinWorkspaceTeam(member.Id, code), model.ErrWorkspaceInvite)
		assertUnchanged()
	}
	require.NoError(t, model.DB.Model(&destinationOwner).Update("status", common.UserStatusDisabled).Error)
	require.ErrorIs(t, model.JoinWorkspaceTeam(member.Id, invite), model.ErrWorkspaceInvite)
	assertUnchanged()
	require.NoError(t, model.DB.Model(&destinationOwner).Update("status", common.UserStatusEnabled).Error)

	// Force a failure after the original membership has been removed. The
	// transaction must restore the old key and allowance as well as membership.
	callbackName := "test:fail_destination_member_key"
	require.NoError(t, model.DB.Callback().Create().Before("gorm:create").Register(callbackName, func(tx *gorm.DB) {
		if token, ok := tx.Statement.Dest.(*model.Token); ok && token.UserId == destinationOwner.Id && token.WorkspaceUserID == member.Id {
			tx.AddError(fmt.Errorf("fixture destination key failure"))
		}
	}))
	err = model.JoinWorkspaceTeam(member.Id, invite)
	require.NoError(t, model.DB.Callback().Create().Remove(callbackName))
	require.ErrorContains(t, err, "fixture destination key failure")
	assertUnchanged()

	_, result := workspaceTeamPolicyCall(t, workspaceTeamPolicyRouter(), member.Id, "/api/workspace/team/join", fmt.Sprintf(`{"code":%q}`, invite))
	require.Equal(t, true, result["success"])
	membership, current, err := model.FindWorkspaceMembership(member.Id)
	require.NoError(t, err)
	require.Equal(t, destination.ID, current.ID)
	require.NotEqual(t, oldKey.Id, membership.TokenID)
	newKey, err := model.GetWorkspaceKey(member.Id, false)
	require.NoError(t, err)
	require.NotEqual(t, oldKey.Key, newKey.Key)
	require.Equal(t, destinationOwner.Id, newKey.UserId)
	require.Zero(t, newKey.RemainQuota, "old member allowance must not follow the member to a new team")
	require.False(t, newKey.UnlimitedQuota)
	var revoked model.Token
	require.NoError(t, model.DB.First(&revoked, oldKey.Id).Error)
	require.Equal(t, common.TokenStatusDisabled, revoked.Status)
	require.Equal(t, oldKey.Key, revoked.Key, "retain old token for settlement and history")
	preservedPersonal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", false)
	require.NoError(t, err)
	require.Equal(t, personal.Key, preservedPersonal.Key)
	var savedHistory model.Log
	require.NoError(t, model.LOG_DB.First(&savedHistory, history.Id).Error)
	require.Equal(t, history, savedHistory)
	var savedAccount model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&savedAccount, "team_id = ?", oldTeam.ID).Error)
	require.Equal(t, oldAccount, savedAccount)
	var savedOwner model.User
	require.NoError(t, model.DB.First(&savedOwner, owner.Id).Error)
	require.Equal(t, owner.Quota, savedOwner.Quota)
	require.ErrorIs(t, model.JoinWorkspaceTeam(member.Id, invite), model.ErrWorkspaceExists)
}

func TestWorkspaceTeamDissolutionSubscriptionPolicy(t *testing.T) {
	for _, test := range []struct {
		name    string
		legacy  bool
		scope   string
		status  string
		future  bool
		expired bool
		blocked bool
	}{
		{"team-active", false, "team", "active", false, false, true},
		{"team-future", false, "team", "active", true, false, true},
		{"team-exhausted", false, "team", "active", false, false, true},
		{"team-expired", false, "team", "active", false, true, false},
		{"team-cancelled", false, "team", "cancelled", false, false, false},
		{"personal-does-not-block-isolated", false, "personal", "active", false, false, false},
		{"other-team-does-not-block", false, "other", "active", false, false, false},
		{"legacy-personal", true, "personal", "active", false, false, true},
		{"legacy-future-personal", true, "personal", "active", true, false, true},
		{"legacy-expired-personal", true, "personal", "active", false, true, false},
		{"legacy-cancelled-personal", true, "personal", "cancelled", false, false, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			owner, member, team, memberKey := setupFundingIsolation(t)
			if test.legacy {
				require.NoError(t, model.DB.Model(team).Update("funding_version", 0).Error)
				team.FundingVersion = 0
			}
			now := common.GetTimestamp()
			sub := model.UserSubscription{UserId: owner.Id, WorkspaceTeamID: team.ID, Status: test.status,
				StartTime: now - 60, EndTime: now + 7200, AmountTotal: 100, WeeklyAmount: 25, WeeklyResetAt: now + 3600}
			if test.scope == "personal" {
				sub.WorkspaceTeamID = 0
			}
			if test.scope == "other" {
				sub.WorkspaceTeamID = team.ID + 999
			}
			if test.future {
				sub.StartTime = now + 3600
			}
			if test.expired {
				sub.EndTime = now - 1
			}
			if test.name == "team-exhausted" {
				sub.AmountUsed = sub.AmountTotal
				sub.WeeklyUsed = sub.WeeklyAmount
			}
			require.NoError(t, model.DB.Create(&sub).Error)
			blocked, err := model.HasUnexpiredWorkspaceTeamSubscription(team)
			require.NoError(t, err)
			require.Equal(t, test.blocked, blocked)
			_, result := workspaceTeamPolicyCall(t, workspaceTeamPolicyRouter(), owner.Id, "/api/workspace/team/leave", fmt.Sprintf(`{"team_id":%d}`, team.ID))
			require.Equal(t, true, result["success"], "an unexpired subscription does not block owner dissolution")
			var savedSub model.UserSubscription
			require.NoError(t, model.DB.First(&savedSub, sub.Id).Error)
			require.Equal(t, sub, savedSub, "leaving must not cancel, consume or delete subscriptions")
			var account model.WorkspaceTeamAccount
			require.NoError(t, model.DB.First(&account, "team_id = ?", team.ID).Error)
			require.Equal(t, true, result["data"].(map[string]any)["dissolved"])
			if !test.legacy {
				require.Positive(t, account.ClosedAt)
			}
			var savedToken model.Token
			require.NoError(t, model.DB.First(&savedToken, memberKey.Id).Error)
			require.Equal(t, common.TokenStatusDisabled, savedToken.Status)
			for _, user := range []model.User{owner, member} {
				var saved model.User
				require.NoError(t, model.DB.First(&saved, user.Id).Error)
				require.Equal(t, user.Quota, saved.Quota, "dissolution neither refunds nor transfers team funding")
				membership, current, err := model.FindWorkspaceMembership(user.Id)
				require.NoError(t, err)
				require.Nil(t, membership)
				require.Nil(t, current)
			}
		})
	}
}
