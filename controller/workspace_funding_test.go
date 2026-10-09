package controller

import (
	"crypto/sha256"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func setupFundingIsolation(t *testing.T) (model.User, model.User, *model.WorkspaceTeam, *model.Token) {
	t.Helper()
	setupTeamDatabase(t)
	batch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = batch })
	owner := model.User{Username: "isolation-owner", AffCode: "iso-owner", Group: "default", Status: 1, Quota: 1000, AuthVersion: 1}
	member := model.User{Username: "isolation-member", AffCode: "iso-member", Group: "default", Status: 1, Quota: 2000, AuthVersion: 1}
	require.NoError(t, model.DB.Create(&owner).Error)
	require.NoError(t, model.DB.Create(&member).Error)
	team, err := model.CreateWorkspaceTeam(owner.Id, "Independent team")
	require.NoError(t, err)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(member.Id, invite))
	cap := 1000
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	key, err := model.GetWorkspaceKey(member.Id, false)
	require.NoError(t, err)
	return owner, member, team, key
}

// Exercise real relay authentication and billing, without sending paid traffic.
func fundingRequest(t *testing.T, key *model.Token, requestID string, estimate, actual int) *httptest.ResponseRecorder {
	t.Helper()
	r := gin.New()
	r.Use(middleware.RequestId())
	r.POST("/v1/responses", middleware.TokenAuth(), func(c *gin.Context) {
		info := relaycommon.GenRelayInfoResponses(c, &dto.OpenAIResponsesRequest{})
		info.RequestId, info.ForcePreConsume = requestID, true
		session, apiErr := service.NewBillingSession(c, info, estimate)
		if apiErr != nil {
			c.Status(apiErr.StatusCode)
			return
		}
		require.NoError(t, session.Settle(actual))
		c.Status(200)
	})
	req := httptest.NewRequest("POST", "/v1/responses", strings.NewReader(`{}`))
	req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(key))
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	return res
}

func TestWorkspaceFundingIsolation(t *testing.T) {
	owner, member, team, memberKey := setupFundingIsolation(t)
	ownerPersonal, err := model.GetWorkspaceKeyForScope(owner.Id, "personal", true)
	require.NoError(t, err)
	ownerTeam, err := model.GetWorkspaceKey(owner.Id, false)
	require.NoError(t, err)
	require.NotEqual(t, ownerPersonal.Key, ownerTeam.Key)
	memberPersonal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	require.NotEqual(t, memberPersonal.Key, memberKey.Key)
	personalSub, err := model.SetWorkspaceSubscription(owner.Id, 600, 200)
	require.NoError(t, err)
	teamSub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 100, 40)
	require.NoError(t, err)
	require.Equal(t, 200, fundingRequest(t, memberKey, "team-spend", 30, 40).Code)
	require.Equal(t, 403, fundingRequest(t, memberKey, "team-empty", 1, 1).Code)
	require.Equal(t, 403, fundingRequest(t, ownerTeam, "owner-team-empty", 0, 1).Code)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	require.Equal(t, 1000, owner.Quota)
	require.Equal(t, 2000, member.Quota)
	require.NoError(t, model.DB.First(personalSub, personalSub.Id).Error)
	require.Zero(t, personalSub.AmountUsed, "team exhaustion must not consume personal allowance")
	require.NoError(t, model.DB.First(teamSub, teamSub.Id).Error)
	require.EqualValues(t, 40, teamSub.AmountUsed)
	require.Equal(t, 200, fundingRequest(t, ownerPersonal, "owner-personal", 10, 12).Code)
	require.Equal(t, 200, fundingRequest(t, memberPersonal, "member-personal", 10, 15).Code)
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	require.Equal(t, 1985, member.Quota)
	require.NoError(t, model.DB.First(personalSub, personalSub.Id).Error)
	require.EqualValues(t, 12, personalSub.AmountUsed)
	personal, err := service.GetWorkspaceSummaryForScope(owner.Id, "personal")
	require.NoError(t, err)
	require.Len(t, personal.Subscriptions, 1)
	require.Equal(t, personalSub.Id, personal.Subscriptions[0].Subscription.Id)
	view, err := service.GetWorkspaceTeamView(member.Id, 0)
	require.NoError(t, err)
	require.Len(t, view.Subscriptions, 1)
	require.Equal(t, teamSub.Id, view.Subscriptions[0].Subscription.Id)
}

func TestWorkspaceFundingMigration(t *testing.T) {
	owner, _, team, memberKey := setupFundingIsolation(t)
	// Reproduce the released schema semantics: the owner's former personal key
	// doubles as the team key, and no independent funding account exists yet.
	require.NoError(t, model.DB.Delete(&model.WorkspaceTeamAccount{}, team.ID).Error)
	require.NoError(t, model.DB.Model(team).Update("funding_version", 0).Error)
	oldOwnerKey, err := model.GetWorkspaceKey(owner.Id, false)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.WorkspacePersonalKey{}).Where("user_id = ?", owner.Id).Update("token_id", oldOwnerKey.Id).Error)
	sub, err := model.SetWorkspaceSubscription(owner.Id, 100000000, 25000000)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"amount_used": 41700, "weekly_used": 41700}).Error)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	before := *sub
	_, err = model.UpdateSubscriptionFirstFunding(owner.Id, "migration-pending", 10, "reserve")
	require.NoError(t, err)
	require.ErrorContains(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{sub.Id}, 0), "unsettled")
	_, err = model.UpdateSubscriptionFirstFunding(owner.Id, "migration-pending", 0, "refund")
	require.NoError(t, err)
	// Snapshot after refund; migration must not even rewrite its update timestamp.
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	before = *sub
	require.Error(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{sub.Id, sub.Id + 999}, 0))
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Equal(t, before, *sub, "partial transfer rolls back")
	for range 2 {
		require.NoError(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{sub.Id}, 0))
	}
	require.Error(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{sub.Id}, 1))
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	expected := before
	expected.WorkspaceTeamID = team.ID
	expected.AllowWalletOverflow = false
	require.Equal(t, expected, *sub)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota, "only the explicitly selected subscription moved")
	personal, err := model.GetWorkspaceKeyForScope(owner.Id, "personal", false)
	require.NoError(t, err)
	require.NotEqual(t, oldOwnerKey.Key, personal.Key)
	stillTeam, err := model.GetWorkspaceKey(owner.Id, false)
	require.NoError(t, err)
	require.Equal(t, oldOwnerKey.Key, stillTeam.Key)
	require.Equal(t, oldOwnerKey.Id, stillTeam.Id)
	require.Equal(t, 200, fundingRequest(t, memberKey, "old-member-continues", 10, 12).Code)
	require.Equal(t, 200, fundingRequest(t, oldOwnerKey, "old-owner-continues", 10, 12).Code)
	personalSubs, err := model.GetAllActiveUserSubscriptions(owner.Id)
	require.NoError(t, err)
	require.Empty(t, personalSubs)
}

// Installer validation checks identity, independently from the two spending
// limits (member allowance and shared team funding). Existing 1.4.3 clients use
// this exact read-only endpoint and must keep accepting a new zero-quota key.
func TestWorkspaceFundingInstallerIdentity(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	zero := 0
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &zero, nil, nil))
	r := gin.New()
	r.GET("/api/usage/token/", middleware.TokenAuthReadOnly(), GetTokenUsage)
	identity := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest("GET", "/api/usage/token/", nil)
		req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(key))
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	response := identity()
	require.Equal(t, 200, response.Code)
	require.Contains(t, response.Body.String(), `"code":true`)
	require.Contains(t, response.Body.String(), `"total_available":0`)
	require.NotEqual(t, 200, fundingRequest(t, key, "zero-member-cap", 1, 1).Code)
	require.Equal(t, 200, identity().Code, "exhaustion must not turn a valid key into KEY_REJECTED")
	cap := 100
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &cap, nil, nil))
	require.Equal(t, 403, fundingRequest(t, key, "zero-team-funds", 1, 1).Code)
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	require.Equal(t, 2000, member.Quota, "personal welcome credit is not a fallback")
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 100, 100)
	require.NoError(t, err)
	require.Equal(t, 200, fundingRequest(t, key, "grant-without-reconfigure", 1, 1).Code)
	_, err = model.AdminDeleteUserSubscription(sub.Id)
	require.ErrorContains(t, err, "retain their settlement ledger")
	require.NoError(t, model.DB.Model(key).Update("status", common.TokenStatusDisabled).Error)
	require.Equal(t, 401, identity().Code, "revocation still prevents identity validation")
}

func TestWorkspaceFundingMemberRemoval(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	personal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	_, err = model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 100, 100)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "removed-in-flight", 20, "reserve")
	require.NoError(t, err)
	r := gin.New()
	r.DELETE("/members/:id", middleware.UserAuth(), WorkspaceSessionRequired, RemoveWorkspaceMember)
	ownerSession, err := service.CreateLoginSession(owner.Id, "password", "127.0.0.1", "remove-test")
	require.NoError(t, err)
	memberSession, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "remove-test")
	require.NoError(t, err)
	remove := func(actor string, target, teamID int, site string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("DELETE", fmt.Sprintf("/members/%d", target), strings.NewReader(fmt.Sprintf(`{"team_id":%d}`, teamID)))
		req.Header.Set("Authorization", "Bearer "+actor)
		req.Header.Set("Sec-Fetch-Site", site)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	for _, args := range []struct {
		actor          string
		target, teamID int
	}{
		{memberSession.AccessToken, owner.Id, team.ID},
		{memberSession.AccessToken, member.Id, team.ID},
		{ownerSession.AccessToken, owner.Id, team.ID},
		{ownerSession.AccessToken, member.Id, team.ID + 100},
	} {
		require.Contains(t, remove(args.actor, args.target, args.teamID, "same-origin").Body.String(), `"success":false`)
	}
	require.Equal(t, 403, remove(ownerSession.AccessToken, member.Id, team.ID, "cross-site").Code)
	require.NotEqual(t, 200, remove(model.WorkspaceKeyText(key), member.Id, team.ID, "same-origin").Code)
	require.Contains(t, remove(ownerSession.AccessToken, member.Id, team.ID, "same-origin").Body.String(), `"success":true`)
	require.Error(t, model.ValidateWorkspaceToken(key))
	require.NoError(t, model.ValidateWorkspaceToken(personal))
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	require.Equal(t, 2000, member.Quota)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "removed-in-flight", 12, "settle")
	require.NoError(t, err)
	view, err := service.GetWorkspaceTeamView(owner.Id, 0)
	require.NoError(t, err)
	require.Len(t, view.Members, 1)
	require.EqualValues(t, 12, view.Subscriptions[0].Subscription.AmountUsed)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(member.Id, invite))
	replacement, err := model.GetWorkspaceKey(member.Id, false)
	require.NoError(t, err)
	require.NotEqual(t, key.Id, replacement.Id)
	require.Error(t, model.ValidateWorkspaceToken(key), "rejoining must not reactivate a revoked key")
	currentPersonal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", false)
	require.NoError(t, err)
	require.Equal(t, personal.Id, currentPersonal.Id)
}

func TestWorkspaceFundingPersonalCheckout(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.AutoMigrate(&model.BillingOrder{}))
	order, err := model.CreateBillingCheckout(member.Id, model.BillingCheckoutRequest{Kind: "paygo", AmountCNY: 1, IdempotencyKey: "personal-member-checkout"})
	require.NoError(t, err)
	_, err = model.ClaimBillingEpay(member.Id, order.TradeNo, "epay:alipay")
	require.NoError(t, err)
	_, err = model.FulfillBillingOrder(member.Id, order.TradeNo, "epay:alipay", order.PriceCents, true)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	require.EqualValues(t, 2000+order.CreditedQuota, member.Quota)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.Zero(t, account.Quota)
}

func TestWorkspaceFundingConcurrent(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	sqlDB, err := model.DB.DB()
	require.NoError(t, err)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		sqlDB.SetMaxOpenConns(12)
	}
	var accepted atomic.Int32
	var wg sync.WaitGroup
	for i := range 40 {
		wg.Go(func() {
			_, err := model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, fmt.Sprintf("concurrent-%d", i), 10, "reserve")
			if err == nil {
				accepted.Add(1)
			}
		})
	}
	wg.Wait()
	require.EqualValues(t, 10, accepted.Load())
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.Zero(t, account.Quota)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 100, sub.AmountUsed)
	require.EqualValues(t, 100, sub.WeeklyUsed)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "concurrent-oversize", common.MaxWalletQuota, "reserve")
	require.Error(t, err)
}

func TestWorkspaceFundingLifecycle(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("quota", 100).Error)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "delayed-team", 50, "reserve")
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "refund-team", 10, "reserve")
	require.NoError(t, err)
	_, err = model.LeaveWorkspaceTeam(member.Id, team.ID)
	require.NoError(t, err)
	require.Error(t, model.ValidateWorkspaceToken(key))
	_, err = model.LeaveWorkspaceTeam(owner.Id, team.ID)
	require.NoError(t, err)
	for range 2 {
		_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "delayed-team", 60, "settle")
		require.NoError(t, err)
		_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "refund-team", 0, "refund")
		require.NoError(t, err)
	}
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "after-close", 1, "reserve")
	require.Error(t, err)
	var account model.WorkspaceTeamAccount
	require.NoError(t, model.DB.First(&account, team.ID).Error)
	require.EqualValues(t, 100, account.Quota)
	require.Positive(t, account.ClosedAt)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Equal(t, "active", sub.Status)
	require.Greater(t, sub.EndTime, common.GetTimestamp())
	require.EqualValues(t, 60, sub.AmountUsed)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
	personal, err := model.GetWorkspaceKey(owner.Id, false)
	require.NoError(t, err)
	require.Zero(t, personal.WorkspaceUserID)
	_, err = model.UpdateSubscriptionFirstFunding(owner.Id, "delayed-team", 60, "settle")
	require.ErrorContains(t, err, "identity mismatch")
}

func TestWorkspaceFundingScopePermissions(t *testing.T) {
	owner, member, team, memberKey := setupFundingIsolation(t)
	personal, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	session, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "isolation-test")
	require.NoError(t, err)
	r := gin.New()
	w := r.Group("/workspace", middleware.UserAuth(), WorkspaceSessionRequired)
	w.GET("", GetWorkspace)
	w.POST("/key/reveal", RevealWorkspaceKey)
	w.POST("/key/rotate", RotateWorkspaceKey)
	call := func(scope, action, credential string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/workspace/key/"+action+"?scope="+scope+fmt.Sprintf("&user_id=%d&team_id=999", owner.Id), strings.NewReader(`{}`))
		req.Header.Set("Authorization", "Bearer "+credential)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	for _, scope := range []string{"personal", "team", "current"} {
		res := call(scope, "reveal", session.AccessToken)
		require.Equal(t, 200, res.Code)
		var data struct {
			Success bool
			Data    struct {
				Key string `json:"api_key"`
			}
		}
		require.NoError(t, common.Unmarshal(res.Body.Bytes(), &data))
		require.True(t, data.Success)
		expected := memberKey.Key
		if scope == "personal" {
			expected = personal.Key
		}
		require.Equal(t, sha256.Sum256([]byte("sk-"+expected)), sha256.Sum256([]byte(data.Data.Key)))
		require.Equal(t, "no-store", res.Header().Get("Cache-Control"))
	}
	require.Contains(t, call("unknown", "reveal", session.AccessToken).Body.String(), `"success":false`)
	require.NotEqual(t, 200, call("personal", "reveal", model.WorkspaceKeyText(memberKey)).Code)
	before := memberKey.Key
	require.Contains(t, call("personal", "rotate", session.AccessToken).Body.String(), `"success":true`)
	require.NoError(t, model.DB.First(memberKey, memberKey.Id).Error)
	require.Equal(t, before, memberKey.Key, "personal rotation must not affect the installed team key")
	_, err = model.SetWorkspaceTeamSubscription(member.Id, team.ID, 100, 50)
	require.ErrorIs(t, err, model.ErrWorkspaceAccess)
	_, err = model.UpdateWorkspaceTeamFunding(member.Id, team.ID, "wrong-owner", 1, "reserve")
	require.Error(t, err)
}

func TestWorkspaceFundingWeeklyAndExpiry(t *testing.T) {
	owner, _, team, _ := setupFundingIsolation(t)
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 100, 40)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "old-week", 20, "reserve")
	require.NoError(t, err)
	// Simulate a new weekly window while preserving lifetime usage.
	require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"weekly_reset_at": sub.WeeklyResetAt + model.BillingWeekSeconds, "weekly_used": 10}).Error)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "old-week", 0, "refund")
	require.NoError(t, err)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.EqualValues(t, 10, sub.WeeklyUsed)
	require.Zero(t, sub.AmountUsed)
	require.NoError(t, model.DB.Model(sub).Update("end_time", common.GetTimestamp()-1).Error)
	_, err = model.UpdateWorkspaceTeamFunding(owner.Id, team.ID, "expired-team", 1, "reserve")
	require.Error(t, err)
	_, err = model.ExpireDueSubscriptions(100)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(sub, sub.Id).Error)
	require.Equal(t, "expired", sub.Status)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	require.Equal(t, 1000, owner.Quota)
}

// Explicitly opt in with a disposable SQLite copy. This test refuses the
// deployed path and creates another private copy through the harness first.
func TestWorkspaceFundingSnapshotMigration(t *testing.T) {
	path := os.Getenv("WORKSPACE_FUNDING_SNAPSHOT")
	if path == "" {
		t.Skip("requires the private disposable online-backup copy")
	}
	absolute, err := filepath.Abs(path)
	require.NoError(t, err)
	require.Contains(t, filepath.ToSlash(absolute), "/realyu-team-funding-lab/.lab/funding-snapshot/")
	require.Equal(t, "test.db", filepath.Base(absolute))
	t.Setenv("SQL_DSN", "")
	t.Setenv("LOG_SQL_DSN", "")
	t.Setenv("REDIS_CONN_STRING", "")
	common.SQLitePath = absolute
	common.IsMasterNode, common.BatchUpdateEnabled, common.RedisEnabled = true, false, false
	require.NoError(t, model.InitDB())
	db, err := model.DB.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = db.Close() })
	var owner model.User
	require.NoError(t, model.DB.First(&owner, "username = ?", "xinchao").Error)
	var team model.WorkspaceTeam
	require.NoError(t, model.DB.First(&team, "owner_user_id = ?", owner.Id).Error)
	var subs []model.UserSubscription
	require.NoError(t, model.DB.Where("user_id = ? AND amount_total = ? AND source = ? AND status = ?", owner.Id, 100000000, "workspace_admin", "active").Find(&subs).Error)
	require.Len(t, subs, 1)
	beforeSub := subs[0]
	var tokens []model.Token
	require.NoError(t, model.DB.Order("id").Find(&tokens).Error)
	var beforeSessions int64
	require.NoError(t, model.DB.Model(&model.UserSession{}).Count(&beforeSessions).Error)
	var beforeSessionRows []model.UserSession
	require.NoError(t, model.DB.Order("sid").Find(&beforeSessionRows).Error)
	beforeSessionHash := sha256.Sum256(fmt.Appendf(nil, "%#v", beforeSessionRows))
	for range 2 {
		require.NoError(t, model.MigrateWorkspaceFunding(owner.Id, team.ID, []int{beforeSub.Id}, 0))
	}
	var afterSub model.UserSubscription
	require.NoError(t, model.DB.First(&afterSub, beforeSub.Id).Error)
	expected := beforeSub
	expected.WorkspaceTeamID = team.ID
	require.Equal(t, expected, afterSub)
	var afterOwner model.User
	require.NoError(t, model.DB.First(&afterOwner, owner.Id).Error)
	require.Equal(t, owner.Quota, afterOwner.Quota)
	for _, before := range tokens {
		var after model.Token
		require.NoError(t, model.DB.First(&after, before.Id).Error)
		require.Equal(t, sha256.Sum256([]byte(before.Key)), sha256.Sum256([]byte(after.Key)), "existing credential digest changed")
		require.Equal(t, before.UserId, after.UserId)
		require.Equal(t, before.WorkspaceUserID, after.WorkspaceUserID)
		require.Equal(t, before.RemainQuota, after.RemainQuota)
		require.Equal(t, before.UsedQuota, after.UsedQuota)
	}
	var afterSessions int64
	require.NoError(t, model.DB.Model(&model.UserSession{}).Count(&afterSessions).Error)
	require.Equal(t, beforeSessions, afterSessions)
	var afterSessionRows []model.UserSession
	require.NoError(t, model.DB.Order("sid").Find(&afterSessionRows).Error)
	require.Equal(t, beforeSessionHash, sha256.Sum256(fmt.Appendf(nil, "%#v", afterSessionRows)), "session records must remain byte-for-byte equivalent")
	require.Equal(t, owner.AuthVersion, afterOwner.AuthVersion)
	t.Logf("snapshot migration: team=%d subscription=%d original_total=%d preserved_used=%d personal_wallet=%d existing_keys=%d sessions=%d", team.ID, beforeSub.Id, beforeSub.AmountTotal, beforeSub.AmountUsed, owner.Quota, len(tokens), beforeSessions)
}

// Legacy fixture deliberately reproduces v3.9.0.3 semantics to protect the
// compatibility phase and already-persisted tasks during a staged migration.
func legacyWorkspaceTeamFixture(ownerID int, name string) (*model.WorkspaceTeam, error) {
	key, err := model.GetWorkspaceKeyForScope(ownerID, "personal", true)
	if err != nil {
		return nil, err
	}
	team := &model.WorkspaceTeam{OwnerUserID: ownerID, Name: name, CreatedAt: common.GetTimestamp()}
	err = model.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(team).Error; err != nil {
			return err
		}
		if err := tx.Model(key).Update("workspace_user_id", ownerID).Error; err != nil {
			return err
		}
		return tx.Create(&model.WorkspaceMember{UserID: ownerID, TeamID: team.ID, TokenID: key.Id, Status: 1, CreatedAt: team.CreatedAt}).Error
	})
	return team, err
}

// A reservation may temporarily empty a key. Authentication must not turn that
// temporary balance into a persistent administrative revocation.
func TestWorkspaceFundingTokenRecovery(t *testing.T) {
	owner, member, team, key := setupFundingIsolation(t)
	teamKey := key
	weeklyCap := 10
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &weeklyCap, nil, nil))
	sub, err := model.SetWorkspaceTeamSubscription(owner.Id, team.ID, 400, 100)
	require.NoError(t, err)
	reset := func(t *testing.T, status, remaining int, expiry int64, unlimited bool) {
		t.Helper()
		require.NoError(t, model.DB.Where("team_id = ?", team.ID).Delete(&model.WorkspaceMemberWeeklyUsage{}).Error)
		require.NoError(t, model.DB.Unscoped().Model(&model.Token{}).Where("id = ?", key.Id).Updates(map[string]any{
			"key": key.Key, "status": status, "remain_quota": remaining, "used_quota": 0,
			"expired_time": expiry, "unlimited_quota": unlimited, "deleted_at": nil,
		}).Error)
		require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("quota", 100).Error)
		require.NoError(t, model.DB.Model(sub).Updates(map[string]any{"amount_used": 0, "weekly_used": 0}).Error)
	}
	for _, action := range []string{"refund", "settle"} {
		t.Run(action+" after concurrent empty-balance authentication", func(t *testing.T) {
			reset(t, common.TokenStatusEnabled, 10, -1, false)
			ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
			info := &relaycommon.RelayInfo{UserId: owner.Id, FundingTeamID: team.ID, TokenId: key.Id, TokenKey: key.Key, RequestId: "token-recovery-" + action, ForcePreConsume: true}
			session, apiErr := service.NewBillingSession(ctx, info, 10)
			require.Nil(t, apiErr)
			_, err := model.ValidateUserToken(key.Key)
			require.NoError(t, err, "independent team key identity must not depend on cumulative token quota")
			remaining, used := 10, int64(0)
			if action == "refund" {
				session.Refund(ctx)
			} else {
				require.NoError(t, session.Settle(2))
				remaining, used = 8, 2
			}
			require.Eventually(t, func() bool {
				var current model.Token
				var account model.WorkspaceTeamAccount
				var currentSub model.UserSubscription
				if model.DB.First(&current, key.Id).Error != nil || model.DB.First(&account, team.ID).Error != nil || model.DB.First(&currentSub, sub.Id).Error != nil {
					return false
				}
				return current.RemainQuota == remaining && account.Quota == 100 && currentSub.AmountUsed == used && currentSub.WeeklyUsed == used
			}, 2e9, 1e7)
			current, err := model.ValidateUserToken(key.Key)
			require.NoError(t, err, "restored allowance must work without an administrator resetting the key")
			require.Equal(t, remaining, current.RemainQuota)
			require.NoError(t, model.ValidateWorkspaceToken(current))
		})
	}
	// Keep the legacy native-token recovery/race coverage on a personal key;
	// independent team members now enforce their cap in the weekly ledger.
	key, err = model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	for _, unlimited := range []bool{false, true} {
		t.Run(fmt.Sprintf("legacy exhausted key unlimited=%v", unlimited), func(t *testing.T) {
			remaining := 10
			if unlimited {
				remaining = 0
			}
			reset(t, common.TokenStatusExhausted, remaining, -1, unlimited)
			current, err := model.ValidateUserToken(key.Key)
			require.NoError(t, err)
			require.Equal(t, common.TokenStatusEnabled, current.Status)
			require.NoError(t, model.DB.First(&current, key.Id).Error)
			require.Equal(t, common.TokenStatusEnabled, current.Status)
		})
	}
	for _, tc := range []struct {
		name              string
		status, remaining int
		expiry            int64
		deleted           bool
	}{
		{"disabled", common.TokenStatusDisabled, 10, -1, false},
		{"expired status", common.TokenStatusExpired, 10, common.GetTimestamp() + 3600, false},
		{"expired deadline", common.TokenStatusExhausted, 10, common.GetTimestamp() - 1, false},
		{"exhausted without funds", common.TokenStatusExhausted, 0, -1, false},
		{"exhausted with debt", common.TokenStatusExhausted, -10, -1, false},
		{"deleted", common.TokenStatusExhausted, 10, -1, true},
	} {
		t.Run(tc.name+" must not recover", func(t *testing.T) {
			reset(t, tc.status, tc.remaining, tc.expiry, false)
			if tc.deleted {
				require.NoError(t, model.DB.Delete(&model.Token{}, key.Id).Error)
			}
			_, err := model.ValidateUserToken(key.Key)
			require.ErrorIs(t, err, model.ErrTokenInvalid)
			var current model.Token
			require.NoError(t, model.DB.Unscoped().First(&current, key.Id).Error)
			require.Equal(t, tc.status, current.Status)
			require.Equal(t, tc.remaining, current.RemainQuota)
			require.Equal(t, tc.deleted, current.DeletedAt.Valid)
		})
	}
	// Inject a competing mutation exactly between the read and conditional write.
	// This runs on the current transaction/connection on every supported engine;
	// it does not rely on scheduling, sleeps or SQLite-only locking behavior.
	for _, tc := range []struct {
		name      string
		mutation  map[string]any
		valid     bool
		remaining int
	}{
		{"administrator disables", map[string]any{"status": common.TokenStatusDisabled}, false, 10},
		{"quota consumed", map[string]any{"remain_quota": 0}, false, 0},
		{"quota changed", map[string]any{"remain_quota": 3}, true, 3},
		{"another request recovers", map[string]any{"status": common.TokenStatusEnabled}, true, 10},
		{"deadline expires", map[string]any{"expired_time": common.GetTimestamp() - 1}, false, 10},
		{"key rotated", map[string]any{"key": "reviewreplacement012345678901234567890"}, false, 10},
	} {
		t.Run(tc.name+" during legacy recovery", func(t *testing.T) {
			reset(t, common.TokenStatusExhausted, 10, -1, false)
			injected := false
			const callback = "review:token_recovery_race"
			require.NoError(t, model.DB.Callback().Update().Before("gorm:update").Register(callback, func(tx *gorm.DB) {
				if injected || tx.Statement.Table != "tokens" {
					return
				}
				injected = true
				err := tx.Session(&gorm.Session{NewDB: true}).Model(&model.Token{}).Where("id = ?", key.Id).Updates(tc.mutation).Error
				if err != nil {
					tx.AddError(err)
				}
			}))
			t.Cleanup(func() { require.NoError(t, model.DB.Callback().Update().Remove(callback)) })
			current, err := model.ValidateUserToken(key.Key)
			require.True(t, injected, "the competing write must happen before recovery")
			if tc.valid {
				require.NoError(t, err)
				require.Equal(t, tc.remaining, current.RemainQuota, "authentication must return current quota, not its earlier snapshot")
			} else {
				require.ErrorIs(t, err, model.ErrTokenInvalid)
			}
			var stored model.Token
			require.NoError(t, model.DB.First(&stored, key.Id).Error)
			require.Equal(t, tc.remaining, stored.RemainQuota)
			expectedStatus := common.TokenStatusExhausted
			if tc.valid {
				expectedStatus = common.TokenStatusEnabled
			} else if status, ok := tc.mutation["status"].(int); ok {
				expectedStatus = status
			}
			require.Equal(t, expectedStatus, stored.Status)
		})
	}
	t.Run("removed membership cannot recover", func(t *testing.T) {
		key = teamKey
		reset(t, common.TokenStatusExhausted, 10, -1, false)
		require.NoError(t, model.RemoveWorkspaceMember(owner.Id, team.ID, member.Id))
		_, err := model.ValidateUserToken(key.Key)
		require.ErrorIs(t, err, model.ErrTokenInvalid)
		// Even an inconsistent legacy token status must not restore membership.
		require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", key.Id).Update("status", common.TokenStatusExhausted).Error)
		_, err = model.ValidateUserToken(key.Key)
		require.ErrorIs(t, err, model.ErrTokenInvalid)
	})
}

func TestWorkspaceFundingSubscriptionManagementRoles(t *testing.T) {
	setupTeamDatabase(t)
	payment := operation_setting.GetPaymentSetting()
	previous := *payment
	payment.ComplianceConfirmed = true
	payment.ComplianceTermsVersion = operation_setting.CurrentComplianceTermsVersion
	t.Cleanup(func() { *payment = previous })
	roles := map[string]int{"root": common.RoleRootUser, "admin": common.RoleAdminUser, "peer": common.RoleAdminUser, "member": common.RoleCommonUser}
	users := make(map[string]model.User)
	sessions := make(map[string]string)
	for name, role := range roles {
		user := model.User{Username: "subscription-role-" + name, AffCode: "sub-role-" + name, Role: role, Status: common.UserStatusEnabled, AuthVersion: 1, Group: "default"}
		require.NoError(t, model.DB.Create(&user).Error)
		session, err := service.CreateLoginSession(user.Id, "password", "127.0.0.1", "subscription-role-test")
		require.NoError(t, err)
		users[name], sessions[name] = user, session.AccessToken
	}
	plan := model.SubscriptionPlan{Title: "Subscription role regression", DurationUnit: "month", DurationValue: 1, TotalAmount: 100, QuotaResetPeriod: "never"}
	require.NoError(t, model.DB.Create(&plan).Error)
	r := gin.New()
	a := r.Group("/admin", middleware.AdminAuth())
	a.GET("/users/:id/subscriptions", AdminListUserSubscriptions)
	a.POST("/users/:id/subscriptions", AdminCreateUserSubscription)
	a.POST("/bind", AdminBindSubscription)
	a.POST("/users/:id/subscriptions/reset", AdminResetUserSubscriptionsByPlan)
	a.POST("/plans/:id/subscriptions/reset", AdminResetPlanSubscriptions)
	a.POST("/subscriptions/:id/invalidate", AdminInvalidateUserSubscription)
	a.DELETE("/subscriptions/:id", AdminDeleteUserSubscription)
	for _, tc := range []struct {
		actor, target string
		allowed       bool
	}{
		{"admin", "peer", false}, {"admin", "root", false},
		{"member", "peer", false}, {"admin", "member", true},
		{"root", "peer", true}, {"root", "root", true},
	} {
		for _, operation := range []string{"list", "create", "bind", "reset", "cancel", "delete"} {
			t.Run(tc.actor+" to "+tc.target+" "+operation, func(t *testing.T) {
				target := users[tc.target]
				now := common.GetTimestamp()
				// Isolate permission checks from the single-active-subscription rule.
				require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ?", target.Id).Update("end_time", now-1).Error)
				sub := model.UserSubscription{UserId: target.Id, PlanId: plan.Id, Source: "admin", Status: "active", StartTime: now, EndTime: now + 86400, AmountTotal: 100, AmountUsed: 40}
				if operation == "create" || operation == "bind" {
					sub.EndTime = now - 1
				}
				require.NoError(t, model.DB.Create(&sub).Error)
				before := sub
				var countBefore int64
				require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ?", target.Id).Count(&countBefore).Error)
				method, url, body := "GET", fmt.Sprintf("/admin/users/%d/subscriptions", target.Id), ""
				switch operation {
				case "create":
					method, body = "POST", fmt.Sprintf(`{"plan_id":%d}`, plan.Id)
				case "bind":
					method, url, body = "POST", "/admin/bind", fmt.Sprintf(`{"user_id":%d,"plan_id":%d}`, target.Id, plan.Id)
				case "reset":
					method, url, body = "POST", url+"/reset", fmt.Sprintf(`{"plan_id":%d,"advance_reset_time":false}`, plan.Id)
				case "cancel":
					method, url = "POST", fmt.Sprintf("/admin/subscriptions/%d/invalidate", sub.Id)
				case "delete":
					method, url = "DELETE", fmt.Sprintf("/admin/subscriptions/%d", sub.Id)
				}
				req := httptest.NewRequest(method, url, strings.NewReader(body))
				req.Header.Set("Authorization", "Bearer "+sessions[tc.actor])
				res := httptest.NewRecorder()
				r.ServeHTTP(res, req)
				var response struct{ Success bool }
				require.NoError(t, common.Unmarshal(res.Body.Bytes(), &response))
				require.Equal(t, tc.allowed, response.Success, "response: %s", res.Body.String())
				if !tc.allowed {
					var after model.UserSubscription
					require.NoError(t, model.DB.First(&after, sub.Id).Error)
					require.Equal(t, before, after, "denied operation must preserve the subscription")
					var countAfter int64
					require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ?", target.Id).Count(&countAfter).Error)
					require.Equal(t, countBefore, countAfter)
					return
				}
				switch operation {
				case "create", "bind":
					var countAfter int64
					require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ?", target.Id).Count(&countAfter).Error)
					require.Equal(t, countBefore+1, countAfter)
					var activeCount int64
					require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("user_id = ? AND workspace_team_id = 0 AND status = ? AND end_time > ?", target.Id, "active", now).Count(&activeCount).Error)
					require.EqualValues(t, 1, activeCount)
				case "reset":
					require.NoError(t, model.DB.First(&sub, sub.Id).Error)
					require.Zero(t, sub.AmountUsed)
				case "cancel":
					require.NoError(t, model.DB.First(&sub, sub.Id).Error)
					require.Equal(t, "cancelled", sub.Status)
				case "delete":
					var countAfter int64
					require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("id = ?", sub.Id).Count(&countAfter).Error)
					require.Zero(t, countAfter)
				}
			})
		}
	}
	for _, actor := range []string{"member", "admin", "root"} {
		t.Run(actor+" global plan reset", func(t *testing.T) {
			rows := []model.UserSubscription{
				{UserId: users["root"].Id, PlanId: plan.Id, Source: "admin", Status: "active", EndTime: common.GetTimestamp() + 86400, AmountTotal: 100, AmountUsed: 40},
				{UserId: users["peer"].Id, WorkspaceTeamID: 77, PlanId: plan.Id, Source: "admin", Status: "active", EndTime: common.GetTimestamp() + 86400, AmountTotal: 100, AmountUsed: 50},
			}
			require.NoError(t, model.DB.Create(&rows).Error)
			req := httptest.NewRequest("POST", fmt.Sprintf("/admin/plans/%d/subscriptions/reset", plan.Id), strings.NewReader(`{"advance_reset_time":false}`))
			req.Header.Set("Authorization", "Bearer "+sessions[actor])
			res := httptest.NewRecorder()
			r.ServeHTTP(res, req)
			var response struct{ Success bool }
			require.NoError(t, common.Unmarshal(res.Body.Bytes(), &response))
			require.Equal(t, actor == "root", response.Success, "response: %s", res.Body.String())
			for _, before := range rows {
				var after model.UserSubscription
				require.NoError(t, model.DB.First(&after, before.Id).Error)
				if actor == "root" {
					require.Zero(t, after.AmountUsed)
				} else {
					require.Equal(t, before, after)
				}
			}
		})
	}
}
