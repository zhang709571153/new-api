package controller

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/service/authz"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWorkspaceProviderFlow(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	oldDisplay := common.DisplayTokenStatEnabled
	common.BatchUpdateEnabled = false
	common.DisplayTokenStatEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch; common.DisplayTokenStatEnabled = oldDisplay })
	users := make([]model.User, 4)
	sessions := make([]string, 4)
	for i := range users {
		pat := fmt.Sprintf("workspace-pat-%d", i)
		users[i] = model.User{Username: fmt.Sprintf("workspace%d", i), DisplayName: fmt.Sprintf("Person %d", i), Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Quota: 5_000_000, Group: "default", AuthVersion: 1, AffCode: fmt.Sprintf("workspace-aff-%d", i), AccessToken: &pat}
		require.NoError(t, model.DB.Create(&users[i]).Error)
		session, err := service.CreateLoginSession(users[i].Id, "password", "127.0.0.1", "workspace-test")
		require.NoError(t, err)
		sessions[i] = session.AccessToken
	}
	owner, member, stranger := users[0].Id, users[1].Id, users[2].Id
	r := gin.New()
	r.Use(middleware.RequestId())
	w := r.Group("/api/workspace", middleware.UserAuth(), WorkspaceSessionRequired)
	w.GET("", GetWorkspace)
	w.GET("/usage", GetWorkspaceUsage)
	w.POST("/key", CreateWorkspaceKey)
	w.POST("/key/reveal", RevealWorkspaceKey)
	w.POST("/key/rotate", RotateWorkspaceKey)
	w.POST("/team", CreateWorkspaceTeam)
	w.GET("/team", GetWorkspaceTeam)
	w.POST("/team/invites", CreateWorkspaceInvite)
	w.POST("/team/join", JoinWorkspaceTeam)
	w.PATCH("/team/members/:id", UpdateWorkspaceMember)
	w.POST("/team/members/:id/key/reveal", RevealWorkspaceKey)
	w.POST("/team/members/:id/key/rotate", RotateWorkspaceKey)
	tokens := r.Group("/api/token", middleware.UserAuth(), middleware.TokenOperationAudit())
	tokens.POST("/", AddToken)
	tokens.PUT("/", UpdateToken)
	tokens.DELETE("/:id", DeleteToken)
	tokens.POST("/batch", DeleteTokenBatch)
	r.GET("/relay-key", middleware.TokenAuth(), func(c *gin.Context) {
		c.JSON(200, gin.H{"user_id": c.GetInt("id"), "workspace_user_id": c.GetInt("workspace_user_id")})
	})
	r.GET("/dashboard/billing/subscription", middleware.TokenAuth(), GetSubscription)
	r.GET("/dashboard/billing/usage", middleware.TokenAuth(), GetUsage)
	r.GET("/v1/tasks/:key", middleware.TokenAuth(), func(c *gin.Context) { c.JSON(200, gin.H{"private_task": "must not reach member"}) })
	call := func(method, path, credential, body string) (*httptest.ResponseRecorder, map[string]any) {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		if credential != "" {
			req.Header.Set("Authorization", "Bearer "+credential)
		}
		response := httptest.NewRecorder()
		r.ServeHTTP(response, req)
		var result map[string]any
		require.NoError(t, common.Unmarshal(response.Body.Bytes(), &result), response.Body.String())
		return response, result
	}
	for _, credential := range []string{"", "workspace-pat-0", "not-a-session"} {
		response, _ := call("POST", "/api/workspace/key", credential, "{}")
		assert.Contains(t, []int{401, 403}, response.Code)
	}
	csrf := httptest.NewRequest("POST", "/api/workspace/team", strings.NewReader(`{"name":"cross-site"}`))
	csrf.Header.Set("Authorization", "Bearer "+sessions[0])
	csrf.Header.Set("Sec-Fetch-Site", "cross-site")
	csrfResponse := httptest.NewRecorder()
	r.ServeHTTP(csrfResponse, csrf)
	assert.Equal(t, 403, csrfResponse.Code)
	_, personal := call("GET", "/api/workspace", sessions[0], "")
	assert.Equal(t, "personal", personal["data"].(map[string]any)["mode"])
	assert.Nil(t, personal["data"].(map[string]any)["api_key"])
	// Restricted historical keys remain intact and are not advertised as the
	// unrestricted singleton. The new key does not change any existing quota.
	allowedIP := "192.0.2.0/24"
	legacy := model.Token{UserId: owner, Key: strings.Repeat("x", 48), Name: "legacy", AllowIps: &allowedIP, Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1}
	require.NoError(t, model.DB.Create(&legacy).Error)
	_, first := call("POST", "/api/workspace/key", sessions[0], "{}")
	require.Equal(t, true, first["success"])
	_, second := call("POST", "/api/workspace/key", sessions[0], "{}")
	assert.Equal(t, first["data"], second["data"], "create is idempotent")
	_, revealed := call("POST", "/api/workspace/key/reveal", sessions[0], "{}")
	ownerKey := revealed["data"].(map[string]any)["api_key"].(string)
	assert.NotEqual(t, "sk-"+legacy.Key, ownerKey)
	response, repeated := call("POST", "/api/workspace/key/reveal", sessions[0], "{}")
	assert.Equal(t, revealed, repeated, "key remains revealable after creation")
	assert.Equal(t, "no-store", response.Header().Get("Cache-Control"))
	response, deniedCreate := call("POST", "/api/workspace/team", sessions[0], `{"name":"Example team"}`)
	require.Equal(t, http.StatusForbidden, response.Code)
	require.Equal(t, false, deniedCreate["success"])
	created, err := model.CreateWorkspaceTeam(owner, "Example team")
	require.NoError(t, err, "test provisioning uses the internal administrator-controlled operation")
	teamID := created.ID
	teamSubscription, err := model.SetWorkspaceTeamSubscription(owner, teamID, 20_000_000, 5_000_000)
	require.NoError(t, err)
	_, invite := call("POST", "/api/workspace/team/invites", sessions[0], "{}")
	require.Equal(t, true, invite["success"])
	code := invite["data"].(map[string]any)["code"].(string)
	var storedInvite model.WorkspaceInvite
	require.NoError(t, model.DB.First(&storedInvite, "team_id = ?", teamID).Error)
	assert.NotEqual(t, code, storedInvite.Hash)
	_, joined := call("POST", "/api/workspace/team/join", sessions[1], fmt.Sprintf(`{"code":%q}`, code))
	require.Equal(t, true, joined["success"])
	_, replay := call("POST", "/api/workspace/team/join", sessions[1], fmt.Sprintf(`{"code":%q}`, code))
	assert.Equal(t, false, replay["success"])
	expiredCode := strings.Repeat("e", 48)
	expiredHash := sha256.Sum256([]byte(expiredCode))
	require.NoError(t, model.DB.Create(&model.WorkspaceInvite{Hash: hex.EncodeToString(expiredHash[:]), TeamID: teamID, ExpiresAt: common.GetTimestamp() - 1}).Error)
	_, expired := call("POST", "/api/workspace/team/join", sessions[2], fmt.Sprintf(`{"code":%q}`, expiredCode))
	assert.Equal(t, false, expired["success"])
	_, duplicate := call("POST", "/api/workspace/team", sessions[1], `{"name":"Second team"}`)
	assert.Equal(t, false, duplicate["success"])
	_, forbidden := call("POST", "/api/workspace/team/invites", sessions[1], `{}`)
	assert.Equal(t, false, forbidden["success"])
	memberPath := fmt.Sprintf("/api/workspace/team/members/%d", member)
	for _, credential := range []string{sessions[1], sessions[2]} {
		_, denied := call("PATCH", memberPath, credential, `{"allowance_usd":99}`)
		assert.Equal(t, false, denied["success"])
		_, denied = call("POST", memberPath+"/key/reveal", credential, `{}`)
		assert.Equal(t, false, denied["success"])
	}
	_, own := call("POST", "/api/workspace/key/reveal", sessions[1], `{}`)
	memberKey := own["data"].(map[string]any)["api_key"].(string)
	response, _ = call("GET", "/relay-key", memberKey, "")
	assert.Equal(t, 200, response.Code, "key identity remains valid before a member is assigned weekly spending permission")
	unfundedToken, err := model.GetWorkspaceKey(member, false)
	require.NoError(t, err)
	assert.Equal(t, 403, fundingRequest(t, unfundedToken, "provider-no-member-allowance", 1, 1).Code)
	_, funded := call("PATCH", memberPath, sessions[0], `{"allowance_usd":2}`)
	require.Equal(t, true, funded["success"])
	_, keyByOwner := call("POST", memberPath+"/key/reveal", sessions[0], `{}`)
	assert.Equal(t, memberKey, keyByOwner["data"].(map[string]any)["api_key"])
	response, identity := call("GET", "/relay-key", memberKey, "")
	require.Equal(t, 200, response.Code, response.Body.String())
	assert.EqualValues(t, owner, identity["user_id"], "legacy identity remains stable; funding is resolved independently")
	assert.EqualValues(t, member, identity["workspace_user_id"], "concurrency belongs to the real member")
	response, _ = call("GET", "/v1/tasks/another-member-task", memberKey, "")
	assert.Equal(t, 403, response.Code, "member keys cannot reach billing-user-scoped task history")
	assert.NotContains(t, response.Body.String(), "private_task")
	memberToken, err := model.GetWorkspaceKey(member, false)
	require.NoError(t, err)
	assert.False(t, memberToken.UnlimitedQuota)
	_, err = model.UpdateWorkspaceTeamFunding(owner, teamID, "provider-spend", 250_000, "reserve", memberToken.Id)
	require.NoError(t, err)
	_, err = model.UpdateWorkspaceTeamFunding(owner, teamID, "provider-spend", 250_000, "settle", memberToken.Id)
	require.NoError(t, err)
	require.NoError(t, model.LOG_DB.Create(&model.Log{UserId: owner, TokenId: memberToken.Id, Type: model.LogTypeConsume, CreatedAt: common.GetTimestamp(), Quota: 250_000, ModelName: "gpt-6-luna", PromptTokens: 10, CompletionTokens: 20, Other: `{"admin_info":{"private":true}}`, Ip: "192.0.2.4"}).Error)
	_, summary := call("GET", "/api/workspace", sessions[1], "")
	memberData := summary["data"].(map[string]any)
	assert.Equal(t, "member", memberData["mode"])
	assert.EqualValues(t, 1.5, memberData["balance_usd"])
	assert.EqualValues(t, 0.5, memberData["used_usd"])
	assert.EqualValues(t, 1, memberData["requests"])
	_, detail := call("GET", "/api/workspace/team", sessions[1], "")
	memberDetail := detail["data"].(map[string]any)
	assert.EqualValues(t, 9.5, memberDetail["pool_balance_usd"])
	require.Len(t, memberDetail["members"], 2)
	for _, item := range memberDetail["members"].([]any) {
		row := item.(map[string]any)
		if row["is_owner"] == true {
			assert.Empty(t, row["masked_key"])
		}
	}
	response, usage := call("GET", "/api/workspace/usage?user_id="+strconv.Itoa(owner), sessions[1], "")
	assert.NotContains(t, response.Body.String(), "private")
	assert.NotContains(t, response.Body.String(), "192.0.2.4")
	assert.NotContains(t, response.Body.String(), "channel")
	assert.EqualValues(t, 1, usage["data"].(map[string]any)["total"])
	_, ownerDetail := call("GET", "/api/workspace/team", sessions[0], "")
	assert.Len(t, ownerDetail["data"].(map[string]any)["members"], 2)
	assert.EqualValues(t, 9.5, ownerDetail["data"].(map[string]any)["pool_balance_usd"])
	_, billingSummary := call("GET", "/dashboard/billing/subscription", memberKey, "")
	assert.EqualValues(t, 2, billingSummary["hard_limit_usd"], "compatibility billing must not expose the owner's pool")
	_, billingUsage := call("GET", "/dashboard/billing/usage", memberKey, "")
	assert.EqualValues(t, 50, billingUsage["total_usage"], "native compatibility usage is the member's usage in cents")
	// Neither legacy token APIs nor forged internal fields can change identity,
	// uncap a member key, or delete the mapping's audit history.
	_, changed := call("PUT", "/api/token/", sessions[0], fmt.Sprintf(`{"id":%d,"name":"changed","unlimited_quota":true,"workspace_user_id":%d}`, memberToken.Id, stranger))
	assert.Equal(t, false, changed["success"])
	_, deleted := call("DELETE", fmt.Sprintf("/api/token/%d", memberToken.Id), sessions[0], "")
	assert.Equal(t, false, deleted["success"])
	_, deleted = call("POST", "/api/token/batch", sessions[0], fmt.Sprintf(`{"ids":[%d]}`, memberToken.Id))
	assert.Equal(t, false, deleted["success"])
	for _, status := range []int{2, 1} {
		_, updated := call("PATCH", memberPath, sessions[0], fmt.Sprintf(`{"status":%d}`, status))
		require.Equal(t, true, updated["success"])
		current, err := model.GetTokenById(memberToken.Id)
		require.NoError(t, err)
		assert.Equal(t, 750_000, current.RemainQuota, "status changes cannot replenish spent allowance")
		response, _ = call("GET", "/relay-key", memberKey, "")
		if status == 2 {
			assert.Equal(t, 401, response.Code)
		} else {
			assert.Equal(t, 200, response.Code)
		}
	}
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", member).Update("status", common.UserStatusDisabled).Error)
	response, _ = call("GET", "/relay-key", memberKey, "")
	assert.Equal(t, 401, response.Code, "disabled member cannot relay through an enabled owner")
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", member).Update("status", common.UserStatusEnabled).Error)
	_, rotated := call("POST", memberPath+"/key/rotate", sessions[0], `{}`)
	require.Equal(t, true, rotated["success"])
	newKey := rotated["data"].(map[string]any)["api_key"].(string)
	assert.NotEqual(t, memberKey, newKey)
	response, _ = call("GET", "/relay-key", memberKey, "")
	assert.Equal(t, 401, response.Code)
	response, _ = call("GET", "/relay-key", newKey, "")
	assert.Equal(t, 200, response.Code)
	current, err := model.GetTokenById(memberToken.Id)
	require.NoError(t, err)
	assert.Equal(t, 750_000, current.RemainQuota)
	assert.Equal(t, 250_000, current.UsedQuota)
	assert.Equal(t, member, current.WorkspaceUserID)
	// Shared pool is the second native admission constraint; member cap is not
	// a separate funded wallet and cannot replenish exhausted team allowance.
	require.NoError(t, model.DB.Model(teamSubscription).Update("weekly_used", 5_000_000).Error)
	_, err = model.UpdateWorkspaceTeamFunding(owner, teamID, "empty-provider", 1, "reserve")
	require.Error(t, err)
	_, emptyPool := call("GET", "/api/workspace", sessions[1], "")
	assert.EqualValues(t, 0, emptyPool["data"].(map[string]any)["balance_usd"])
	personalUser, err := model.GetUserById(member, false)
	require.NoError(t, err)
	assert.Equal(t, 5_000_000, personalUser.Quota, "joining never transfers or destroys the member's personal wallet")
	assert.Error(t, model.DB.Create(&model.WorkspaceTeam{OwnerUserID: owner, Name: "duplicate"}).Error)
	assert.Error(t, model.DB.Create(&model.WorkspaceMember{UserID: member, TeamID: teamID, TokenID: legacy.Id}).Error)
	assert.Error(t, model.DB.Create(&model.WorkspacePersonalKey{UserID: owner, TokenID: legacy.Id}).Error)
	assert.Error(t, model.DB.Create(&storedInvite).Error)
	var auditEntries []model.AuditLog
	require.NoError(t, model.LOG_DB.Find(&auditEntries).Error)
	auditJSON, err := common.Marshal(auditEntries)
	require.NoError(t, err)
	for _, secret := range []string{code, ownerKey, memberKey, newKey, strings.TrimPrefix(memberKey, "sk-")} {
		assert.NotContains(t, string(auditJSON), secret)
	}
	require.NoError(t, model.DB.Delete(&model.User{Id: member}).Error)
	response, _ = call("GET", "/relay-key", newKey, "")
	assert.Equal(t, 401, response.Code)
	history, err := service.GetWorkspaceTeamView(owner, 0)
	require.NoError(t, err)
	require.Len(t, history.Members, 2)
	assert.Equal(t, common.UserStatusDisabled, history.Members[1].Status)
	assert.EqualValues(t, 0.5, history.Members[1].UsedUSD, "deleting an account must not erase team attribution")
	require.NoError(t, model.DB.Delete(&model.User{Id: owner}).Error)
	history, err = service.GetWorkspaceTeamView(stranger, teamID)
	require.NoError(t, err, "supplier report survives a deleted team owner")
	assert.Zero(t, *history.PoolBalanceUSD)
}

func TestWorkspaceTeamLifecycle(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	// Representative old schema upgrade, including a one-use invitation row.
	require.NoError(t, model.DB.Migrator().DropColumn(&model.WorkspaceInvite{}, "Code"))
	legacyCode := strings.Repeat("l", 48)
	hash := sha256.Sum256([]byte(legacyCode))
	require.NoError(t, model.DB.Table("workspace_invites").Create(map[string]any{"hash": hex.EncodeToString(hash[:]), "team_id": 999, "expires_at": common.GetTimestamp() + 3600, "used_by": 0}).Error)
	for range 2 {
		require.NoError(t, model.DB.AutoMigrate(&model.WorkspaceMemberWeeklyUsage{}))
		require.NoError(t, model.DB.AutoMigrate(&model.WorkspaceInvite{}))
	}
	var legacy model.WorkspaceInvite
	require.NoError(t, model.DB.First(&legacy, "hash = ?", hex.EncodeToString(hash[:])).Error)
	assert.Empty(t, legacy.Code)
	require.NoError(t, model.LOG_DB.Migrator().DropColumn(&model.Log{}, "WorkspaceTeamID"))
	for range 2 {
		require.NoError(t, model.LOG_DB.AutoMigrate(&model.Log{}))
	}

	users := make([]model.User, 4)
	personal := make([]*model.Token, 4)
	sessions := make([]string, 4)
	for i := range users {
		users[i] = model.User{Username: fmt.Sprintf("lifecycle%d", i), DisplayName: fmt.Sprintf("Member %d", i), Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Quota: 5_000_000, Group: "default", AuthVersion: 1, AffCode: fmt.Sprintf("lc-%d", i)}
		require.NoError(t, model.DB.Create(&users[i]).Error)
		var err error
		personal[i], err = model.GetWorkspaceKey(users[i].Id, true)
		require.NoError(t, err)
		session, err := service.CreateLoginSession(users[i].Id, "password", "127.0.0.1", "lifecycle")
		require.NoError(t, err)
		sessions[i] = session.AccessToken
	}
	owner := users[0].Id
	team, err := legacyWorkspaceTeamFixture(owner, "Lifecycle team")
	require.NoError(t, err)
	code, expiry, err := model.CreateWorkspaceInvite(owner)
	require.NoError(t, err)
	assert.Zero(t, expiry)
	same, _, err := model.CreateWorkspaceInvite(owner)
	require.NoError(t, err)
	assert.Equal(t, code, same)
	for _, user := range users[1:3] {
		require.NoError(t, model.JoinWorkspaceTeam(user.Id, code))
	}
	_, _, err = model.GetWorkspaceInvite(users[1].Id, true)
	assert.ErrorIs(t, err, model.ErrWorkspaceAccess)
	rotated, _, err := model.GetWorkspaceInvite(owner, true)
	require.NoError(t, err)
	assert.NotEqual(t, code, rotated)
	assert.ErrorIs(t, model.JoinWorkspaceTeam(users[3].Id, code), model.ErrWorkspaceInvite)
	key, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	cap := 50000
	require.NoError(t, model.UpdateWorkspaceMember(owner, users[1].Id, &cap, nil, nil))
	now := common.GetTimestamp()
	for _, log := range []model.Log{
		{UserId: owner, TokenId: personal[0].Id, Type: model.LogTypeConsume, CreatedAt: now, Quota: 100, ModelName: "gpt-6-astra", PromptTokens: 100, CompletionTokens: 10},
		{UserId: owner, TokenId: key.Id, Type: model.LogTypeConsume, CreatedAt: now, Quota: 200, ModelName: "gpt-6-luna", PromptTokens: 200, CompletionTokens: 20},
		{UserId: owner, TokenId: personal[0].Id, Type: model.LogTypeConsume, CreatedAt: team.CreatedAt - 10, Quota: 999, ModelName: "gpt-6-astra"},
		{UserId: users[3].Id, TokenId: personal[3].Id, Type: model.LogTypeConsume, CreatedAt: now, Quota: 888, ModelName: "private-model"},
	} {
		require.NoError(t, model.LOG_DB.Create(&log).Error)
	}
	r := gin.New()
	w := r.Group("/api/workspace", middleware.UserAuth(), WorkspaceSessionRequired)
	w.POST("/team/leave", LeaveWorkspaceTeam)
	w.GET("/team/usage", GetWorkspaceTeamTrends)
	call := func(index int, method, path, payload string) map[string]any {
		req := httptest.NewRequest(method, path, strings.NewReader(payload))
		req.Header.Set("Authorization", "Bearer "+sessions[index])
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		var data map[string]any
		require.NoError(t, common.Unmarshal(res.Body.Bytes(), &data), res.Body.String())
		return data
	}
	path := fmt.Sprintf("/api/workspace/team/usage?start_timestamp=%d&end_timestamp=%d&user_id=%d", now-100, now+60, users[3].Id)
	assert.Equal(t, false, call(1, "GET", path, "")["success"])
	assert.Equal(t, false, call(3, "POST", "/api/workspace/team/leave", fmt.Sprintf(`{"team_id":%d}`, team.ID))["success"])
	assert.Equal(t, true, call(0, "GET", path, "")["success"])
	trends, err := model.GetWorkspaceTeamTrends(owner, now-100, now+60)
	require.NoError(t, err)
	require.Len(t, trends, 2)
	var total int
	for _, row := range trends {
		total += row.Quota
		assert.NotZero(t, row.UserID)
		assert.Zero(t, row.TokenID)
	}
	assert.Equal(t, 300, total)
	view, err := service.GetWorkspaceTeamView(owner, 0)
	require.NoError(t, err)
	assert.Equal(t, float64(300)/common.QuotaPerUnit, view.UsedUSD)
	assert.Equal(t, float64(100)/common.QuotaPerUnit, view.Members[0].UsedUSD, "pre-team personal spending is excluded")
	ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
	ctx.Request = httptest.NewRequest("POST", "/v1/responses", nil)
	startBilling := func() *service.BillingSession {
		info := &relaycommon.RelayInfo{UserId: owner, TokenId: key.Id, TokenKey: key.Key, TokenUnlimited: false, OriginModelName: "gpt-6-luna", ForcePreConsume: true, UserSetting: dto.UserSetting{BillingPreference: "wallet_only"}}
		session, apiErr := service.NewBillingSession(ctx, info, 100)
		require.Nil(t, apiErr)
		return session
	}
	inFlight, cancelled := startBilling(), startBilling()
	assert.Equal(t, false, call(1, "POST", "/api/workspace/team/leave", fmt.Sprintf(`{"team_id":%d}`, team.ID+99))["success"])
	left := call(1, "POST", "/api/workspace/team/leave", fmt.Sprintf(`{"team_id":%d}`, team.ID))
	require.Equal(t, true, left["success"])
	assert.Equal(t, false, left["data"].(map[string]any)["dissolved"])
	assert.ErrorIs(t, model.ValidateWorkspaceToken(key), model.ErrTokenInvalid, "stale cached key is rejected")
	restored, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	assert.Equal(t, personal[1].Key, restored.Key)
	usage, err := model.GetWorkspaceUsage(0, users[1].Id)
	require.NoError(t, err)
	assert.EqualValues(t, 200, usage.UsedQuota)
	trends, err = model.GetWorkspaceTeamTrends(owner, now-100, now+60)
	require.NoError(t, err)
	require.Len(t, trends, 2, "former member remains in team analytics")
	view, err = service.GetWorkspaceTeamView(owner, 0)
	require.NoError(t, err)
	assert.Equal(t, float64(300)/common.QuotaPerUnit, view.UsedUSD, "team total does not drop after departure")
	// Actual BillingSession settlement and refund remain on the original wallet.
	require.NoError(t, inFlight.Settle(130))
	require.NoError(t, inFlight.Settle(130))
	cancelled.Refund(ctx)
	cancelled.Refund(ctx)
	require.Eventually(t, func() bool {
		var saved model.Token
		var wallet model.User
		return model.DB.First(&saved, key.Id).Error == nil && model.DB.First(&wallet, owner).Error == nil && saved.RemainQuota == cap-130 && wallet.Quota == users[0].Quota-130
	}, 5*time.Second, 10*time.Millisecond)
	var retired model.Token
	require.NoError(t, model.DB.First(&retired, key.Id).Error)
	assert.Equal(t, common.TokenStatusDisabled, retired.Status)
	assert.Equal(t, cap-130, retired.RemainQuota)
	require.NoError(t, model.JoinWorkspaceTeam(users[1].Id, rotated))
	newMemberKey, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	assert.NotEqual(t, key.Id, newMemberKey.Id)
	result := call(0, "POST", "/api/workspace/team/leave", fmt.Sprintf(`{"team_id":%d}`, team.ID))
	require.Equal(t, true, result["success"])
	assert.Equal(t, true, result["data"].(map[string]any)["dissolved"])
	for i, user := range users {
		member, team, err := model.FindWorkspaceMembership(user.Id)
		require.NoError(t, err)
		assert.Nil(t, member)
		assert.Nil(t, team)
		key, err := model.GetWorkspaceKey(user.Id, false)
		require.NoError(t, err)
		assert.Equal(t, personal[i].Key, key.Key)
		var saved model.User
		require.NoError(t, model.DB.First(&saved, user.Id).Error)
		expectedQuota := user.Quota
		if user.Id == owner {
			expectedQuota -= 130
		}
		assert.Equal(t, expectedQuota, saved.Quota, "only the in-flight bill changes the original owner's funds")
	}
	assert.ErrorIs(t, model.ValidateWorkspaceToken(newMemberKey), model.ErrTokenInvalid)
	assert.ErrorIs(t, model.JoinWorkspaceTeam(users[3].Id, rotated), model.ErrWorkspaceInvite)
	usage, err = model.GetWorkspaceUsage(0, owner)
	require.NoError(t, err)
	assert.EqualValues(t, 1099, usage.UsedQuota, "owner personal history excludes former members")
	usage, err = model.GetWorkspaceUsage(0, users[1].Id)
	require.NoError(t, err)
	assert.EqualValues(t, 200, usage.UsedQuota)
	// Expiring legacy invitations remain one-use after schema upgrade.
	second, err := legacyWorkspaceTeamFixture(owner, "Next team")
	require.NoError(t, err)
	assert.NotEqual(t, team.ID, second.ID, "team identities must not be reused")
	// An old in-flight request completes after another team has been created.
	late := model.Log{UserId: owner, TokenId: personal[0].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, CreatedAt: common.GetTimestamp() + 1, Quota: 700}
	require.NoError(t, model.LOG_DB.Create(&late).Error)
	var historical []model.Log
	require.NoError(t, model.LOG_DB.Where("workspace_team_id = 0 AND user_id = ?", owner).Find(&historical).Error)
	for _, log := range historical {
		require.NoError(t, model.LOG_DB.Model(&log).Update("workspace_team_id", team.ID).Error)
	}
	view, err = service.GetWorkspaceTeamView(owner, 0)
	require.NoError(t, err)
	assert.Zero(t, view.UsedUSD, "late old-team consumption never enters a new team")
	require.NoError(t, model.DB.Model(&model.WorkspaceInvite{}).Where("hash = ?", legacy.Hash).Update("team_id", second.ID).Error)
	require.NoError(t, model.JoinWorkspaceTeam(users[1].Id, legacyCode))
	assert.ErrorIs(t, model.JoinWorkspaceTeam(users[2].Id, legacyCode), model.ErrWorkspaceInvite)
	missing, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	require.NoError(t, model.DB.Unscoped().Delete(missing).Error)
	require.NoError(t, model.DB.Unscoped().Delete(&users[1]).Error)
	_, err = model.LeaveWorkspaceTeam(owner, second.ID)
	require.NoError(t, err, "hard-deleted member must not trap the remaining team")
	var tombstone model.Token
	require.NoError(t, model.DB.Unscoped().First(&tombstone, missing.Id).Error)
	assert.True(t, tombstone.DeletedAt.Valid)
	assert.Equal(t, common.TokenStatusDisabled, tombstone.Status)
	assert.Equal(t, users[1].Id, tombstone.WorkspaceUserID)
	// Admin deletion of a funding owner keeps former members' receipt attribution.
	require.NoError(t, model.HardDeleteUserById(owner))
	var deletedOwner model.User
	require.NoError(t, model.DB.Unscoped().First(&deletedOwner, owner).Error)
	assert.True(t, deletedOwner.DeletedAt.Valid)
	usage, err = model.GetWorkspaceUsage(0, users[1].Id)
	require.NoError(t, err)
	assert.EqualValues(t, 200, usage.UsedQuota)
}

func TestWorkspaceDeletedKeyRegistration(t *testing.T) {
	setupTeamDatabase(t)
	old := model.User{Username: "old-workspace", Role: common.RoleCommonUser, Status: common.UserStatusDisabled}
	require.NoError(t, model.DB.Create(&old).Error)
	oldKey, err := model.GetWorkspaceKey(old.Id, true)
	require.NoError(t, err)
	require.NoError(t, model.DB.Unscoped().Delete(oldKey).Error)
	// Reproduce the legacy SQLite rowid-reuse case after a physical cleanup.
	require.NoError(t, model.PreserveWorkspaceTokenIDs())
	require.NoError(t, model.PreserveWorkspaceTokenIDs())
	user := model.User{Username: "new-workspace", Password: "Workspace-test-password-2026", Role: common.RoleCommonUser, Status: common.UserStatusEnabled}
	require.NoError(t, user.Insert(0))
	key, err := model.GetWorkspaceKey(user.Id, false)
	require.NoError(t, err)
	require.NotNil(t, key, "registration provisions its personal key atomically")
	assert.NotEqual(t, oldKey.Id, key.Id)
	assert.Zero(t, key.UsedQuota)
	assert.Equal(t, common.QuotaForNewUser, user.Quota, "provisioning never grants extra balance")
	team, err := model.CreateWorkspaceTeam(user.Id, "白夜后援会")
	require.NoError(t, err)
	assert.Equal(t, "白夜后援会", team.Name)
	repeated, err := model.GetWorkspaceKey(user.Id, true)
	require.NoError(t, err)
	assert.NotEqual(t, key.Id, repeated.Id, "team creation provisions a separate key")
	personal, err := model.GetWorkspaceKeyForScope(user.Id, "personal", false)
	require.NoError(t, err)
	assert.Equal(t, key.Id, personal.Id, "the automatic personal key is preserved")
}

func TestWorkspaceUsageIsolation(t *testing.T) {
	setupTeamDatabase(t)
	require.NoError(t, model.DB.AutoMigrate(&model.QuotaData{}))
	users := []model.User{
		{Username: "usage-owner", Role: common.RoleCommonUser, Status: common.UserStatusEnabled},
		{Username: "usage-member", Role: common.RoleCommonUser, Status: common.UserStatusEnabled},
		{Username: "usage-stranger", Role: common.RoleCommonUser, Status: common.UserStatusEnabled},
	}
	sessions := make([]string, len(users))
	for i := range users {
		users[i].AffCode = fmt.Sprintf("usage-aff-%d", i)
		require.NoError(t, model.DB.Create(&users[i]).Error)
		session, err := service.CreateLoginSession(users[i].Id, "password", "127.0.0.1", "usage-test")
		require.NoError(t, err)
		sessions[i] = session.AccessToken
	}
	team, err := model.CreateWorkspaceTeam(users[0].Id, "Usage team")
	require.NoError(t, err)
	code, _, err := model.CreateWorkspaceInvite(users[0].Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(users[1].Id, code))
	ownerKey, err := model.GetWorkspaceKey(users[0].Id, false)
	require.NoError(t, err)
	memberKey, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	now := common.GetTimestamp()
	logs := []model.Log{
		{UserId: users[0].Id, TokenId: ownerKey.Id, Quota: 100, RequestId: "owner-request"},
		{UserId: users[0].Id, TokenId: memberKey.Id, Quota: 200, RequestId: "member-request"},
		{UserId: users[1].Id, TokenId: 0, Quota: 300, RequestId: "pre-team-request"},
		{UserId: users[2].Id, TokenId: 0, Quota: 400, RequestId: "stranger-request"},
	}
	for i := range logs {
		logs[i].Type, logs[i].ModelName, logs[i].CreatedAt = model.LogTypeConsume, "gpt-6-luna", now
		logs[i].PromptTokens, logs[i].CompletionTokens = 10, 2
		logs[i].Other = `{"cache_tokens":0,"admin_info":{"private":"hidden","usage_billing_path":"upstream"}}`
		require.NoError(t, model.LOG_DB.Create(&logs[i]).Error)
		require.NoError(t, model.DB.Create(&model.QuotaData{UserID: logs[i].UserId, TokenID: logs[i].TokenId, ModelName: logs[i].ModelName, CreatedAt: now, Count: 1, Quota: logs[i].Quota, TokenUsed: 12}).Error)
	}
	router := gin.New()
	router.GET("/api/log/self", middleware.UserAuth(), GetUserLogs)
	router.GET("/api/log/self/stat", middleware.UserAuth(), GetLogsSelfStat)
	router.GET("/api/data/self", middleware.UserAuth(), GetUserQuotaDates)
	for i, expected := range []int{100, 500, 400} {
		for _, path := range []string{"/api/log/self", "/api/log/self/stat", "/api/data/self"} {
			req := httptest.NewRequest("GET", fmt.Sprintf("%s?start_timestamp=%d&end_timestamp=%d&user_id=%d&username=usage-owner&type=2", path, now-60, now+60, users[0].Id), nil)
			req.Header.Set("Authorization", "Bearer "+sessions[i])
			res := httptest.NewRecorder()
			router.ServeHTTP(res, req)
			var body map[string]any
			require.NoError(t, common.Unmarshal(res.Body.Bytes(), &body))
			require.Equal(t, true, body["success"], path)
			sum := 0
			switch path {
			case "/api/log/self":
				for _, item := range body["data"].(map[string]any)["items"].([]any) {
					sum += int(item.(map[string]any)["quota"].(float64))
				}
				assert.NotContains(t, res.Body.String(), "hidden")
				assert.Contains(t, res.Body.String(), "usage_estimated")
			case "/api/log/self/stat":
				sum = int(body["data"].(map[string]any)["quota"].(float64))
			case "/api/data/self":
				for _, item := range body["data"].([]any) {
					sum += int(item.(map[string]any)["quota"].(float64))
				}
			}
			assert.Equal(t, expected, sum, "user %d %s must retain personal history and exclude other members", i, path)
		}
	}
	// A corrupted/reused reference must never disclose another user's credential.
	require.NoError(t, model.DB.Model(memberKey).Update("workspace_user_id", users[2].Id).Error)
	_, err = model.GetWorkspaceKey(users[1].Id, false)
	assert.Error(t, err)
	_, err = model.GetOwnedWorkspaceMemberToken(team.OwnerUserID, users[1].Id)
	assert.Error(t, err)
}

// External runs require dedicated, disposable databases. Keeping logs in a
// separate database also protects against accidentally adding a cross-DB join.
func TestWorkspaceTeamRequestLogs(t *testing.T) {
	setupTeamDatabase(t)
	now := common.GetTimestamp()
	users := make([]model.User, 5)
	sessions := make([]string, len(users))
	for i := range users {
		users[i] = model.User{Username: fmt.Sprintf("request-member-%d", i), DisplayName: fmt.Sprintf("Caller %d", i), Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1, AffCode: fmt.Sprintf("req%d", i)}
		if i == 4 {
			users[i].Role = common.RoleAdminUser
		}
		require.NoError(t, model.DB.Create(&users[i]).Error)
		session, err := service.CreateLoginSession(users[i].Id, "password", "", "team-request-test")
		require.NoError(t, err)
		sessions[i] = session.AccessToken
	}
	team := model.WorkspaceTeam{OwnerUserID: users[0].Id, Name: "Request team", CreatedAt: now - 100}
	otherTeam := model.WorkspaceTeam{OwnerUserID: users[3].Id, Name: "Other team", CreatedAt: now - 100}
	require.NoError(t, model.DB.Create(&team).Error)
	require.NoError(t, model.DB.Create(&otherTeam).Error)
	tokens := make([]model.Token, 4)
	for i := range tokens {
		owner := users[0].Id
		teamID := team.ID
		if i == 3 {
			owner, teamID = users[3].Id, otherTeam.ID
		}
		tokens[i] = model.Token{UserId: owner, WorkspaceUserID: users[i].Id, Key: fmt.Sprintf("private-team-key-%d", i), Name: fmt.Sprintf("key-%d", i), Status: common.TokenStatusEnabled}
		require.NoError(t, model.DB.Create(&tokens[i]).Error)
		if i != 2 {
			require.NoError(t, model.DB.Create(&model.WorkspaceMember{UserID: users[i].Id, TeamID: teamID, TokenID: tokens[i].Id, Status: common.UserStatusEnabled}).Error)
		}
	}
	// Retired keys and deleted accounts must remain attributable without granting access.
	require.NoError(t, model.DB.Delete(&tokens[2]).Error)
	require.NoError(t, model.DB.Delete(&users[2]).Error)
	rows := []model.Log{
		{TokenId: tokens[0].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, Quota: 10, ModelName: "gpt-6-astra", RequestId: "owner"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, Quota: 20, ModelName: "gpt-6.1-sol", RequestId: "member", UpstreamRequestId: "up-member"},
		{TokenId: tokens[2].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeConsume, Quota: 30, ModelName: "gpt-6-luna", RequestId: "former"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeError, RequestId: "error"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeRefund, Quota: 5, RequestId: "refund"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: 0, Type: model.LogTypeConsume, Quota: 40, ModelName: "gpt-6.1-sol", RequestId: "legacy"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: -1, Type: model.LogTypeConsume, Quota: 900, RequestId: "private"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: otherTeam.ID, Type: model.LogTypeConsume, Quota: 900, RequestId: "previous-team"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: 0, Type: model.LogTypeConsume, Quota: 900, CreatedAt: team.CreatedAt - 1, RequestId: "before-team"},
		{TokenId: tokens[1].Id, WorkspaceTeamID: team.ID, Type: model.LogTypeLogin, RequestId: "private-login"},
		{TokenId: tokens[3].Id, WorkspaceTeamID: otherTeam.ID, Type: model.LogTypeConsume, Quota: 900, UserId: users[3].Id, RequestId: "other-team"},
	}
	for i := range rows {
		if rows[i].UserId == 0 {
			rows[i].UserId = users[0].Id
		}
		if rows[i].CreatedAt == 0 {
			rows[i].CreatedAt = now
		}
		rows[i].PromptTokens, rows[i].CompletionTokens = 10, 1
		rows[i].TokenName, rows[i].Group = "named-key", "default"
		rows[i].Ip, rows[i].ChannelId = "192.0.2.99", 99
		rows[i].Other = `{"admin_info":{"private":"admin-secret"},"root_info":{"private":"root-secret"},"cache_tokens":4}`
		require.NoError(t, model.LOG_DB.Create(&rows[i]).Error)
	}
	r := gin.New()
	w := r.Group("/api/workspace", middleware.UserAuth(), WorkspaceSessionRequired)
	w.GET("/team/logs", GetWorkspaceTeamLogs)
	w.GET("/team/logs/stat", GetWorkspaceTeamLogStats)
	r.GET("/api/log/self", middleware.UserAuth(), GetUserLogs)
	call := func(credential, path string) map[string]any {
		t.Helper()
		req := httptest.NewRequest("GET", path, nil)
		req.Header.Set("Authorization", "Bearer "+credential)
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		var result map[string]any
		require.NoError(t, common.Unmarshal(res.Body.Bytes(), &result))
		for _, secret := range []string{"admin-secret", "root-secret", "private-team-key"} {
			assert.NotContains(t, res.Body.String(), secret)
		}
		if strings.HasPrefix(path, "/api/workspace/") {
			assert.NotContains(t, res.Body.String(), "192.0.2.99")
		}
		return result
	}
	base := "/api/workspace/team/logs"
	for _, credential := range []string{sessions[1], sessions[4], "private-team-key-0", ""} {
		for _, path := range []string{base, base + "/stat"} {
			assert.Equal(t, false, call(credential, path)["success"], "only the authenticated owner can read team details")
		}
	}
	result := call(sessions[0], base+"?team_id="+strconv.Itoa(otherTeam.ID)+"&user_id="+strconv.Itoa(users[3].Id))
	require.Equal(t, true, result["success"])
	data := result["data"].(map[string]any)
	assert.EqualValues(t, 6, data["total"])
	items := data["items"].([]any)
	require.Len(t, items, 6)
	for _, item := range items {
		row := item.(map[string]any)
		assert.Contains(t, []string{"owner", "member", "former", "error", "refund", "legacy"}, row["request_id"])
		assert.EqualValues(t, 0, row["channel"])
		assert.EqualValues(t, 0, row["token_id"])
		assert.Contains(t, row["other"], "cache_tokens")
		if row["request_id"] == "member" {
			assert.EqualValues(t, users[1].Id, row["user_id"])
			assert.Equal(t, "Caller 1", row["username"])
		}
	}
	require.Len(t, data["members"], 3)
	assert.Equal(t, true, data["members"].([]any)[2].(map[string]any)["former"])
	for _, tc := range []struct {
		query string
		total int
		quota int
	}{
		{"", 6, 100},
		{"?member_id=" + strconv.Itoa(users[1].Id), 4, 60},
		{"?member_id=" + strconv.Itoa(users[2].Id), 1, 30},
		{"?member_id=" + strconv.Itoa(users[3].Id), 0, 0},
		{"?model_name=gpt-6.1-sol", 2, 60},
		{"?request_id=member", 1, 20},
		{"?upstream_request_id=up-member", 1, 20},
		{"?token_name=wrong", 0, 0},
		{"?group=wrong", 0, 0},
		{"?type=5", 1, 0},
		{"?end_timestamp=" + strconv.FormatInt(now-1, 10), 0, 0},
	} {
		actual := call(sessions[0], base+tc.query)
		require.Equal(t, true, actual["success"], tc.query)
		assert.EqualValues(t, tc.total, actual["data"].(map[string]any)["total"], tc.query)
		stat := call(sessions[0], base+"/stat"+tc.query)
		require.Equal(t, true, stat["success"], tc.query)
		assert.EqualValues(t, tc.quota, stat["data"].(map[string]any)["quota"], tc.query)
	}
	page := call(sessions[0], base+"?p=2&page_size=2")["data"].(map[string]any)
	assert.EqualValues(t, 6, page["total"])
	require.Len(t, page["items"], 2)
	assert.Equal(t, "error", page["items"].([]any)[0].(map[string]any)["request_id"])
	for _, invalid := range []string{"?member_id=-1", "?member_id=bad", "?type=7", "?start_timestamp=200&end_timestamp=100"} {
		assert.Equal(t, false, call(sessions[0], base+invalid)["success"])
	}
	other := call(sessions[3], base+"?team_id="+strconv.Itoa(team.ID))["data"].(map[string]any)
	assert.EqualValues(t, 1, other["total"])
	self := call(sessions[0], "/api/log/self")["data"].(map[string]any)
	require.Len(t, self["items"], 1, "Only Mine continues to exclude teammates")
	var persisted model.Log
	require.NoError(t, model.LOG_DB.First(&persisted, rows[1].Id).Error)
	assert.Equal(t, users[0].Id, persisted.UserId, "display attribution must not mutate the funding ledger")
}

func setupTeamDatabase(t *testing.T) {
	t.Helper()
	previousDB, previousLogDB := model.DB, model.LOG_DB
	previousMain, previousLog := common.MainDatabaseType(), common.LogDatabaseType()
	previousRedis, previousMaster := common.RedisEnabled, common.IsMasterNode
	previousSQLite, previousMemory := common.SQLitePath, common.MemoryCacheEnabled
	engine := os.Getenv("TEAM_TEST_DB_ENGINE")
	require.Contains(t, []string{"", "sqlite", "mysql", "postgres"}, engine)
	mainDSN, logDSN := os.Getenv("TEAM_TEST_DSN"), os.Getenv("TEAM_TEST_LOG_DSN")
	if engine == "mysql" || engine == "postgres" {
		require.NotEmpty(t, mainDSN)
		require.NotEmpty(t, logDSN)
	} else {
		mainDSN, logDSN = "local", "local"
	}
	t.Setenv("SQL_DSN", mainDSN)
	t.Setenv("LOG_SQL_DSN", logDSN)
	common.RedisEnabled, common.IsMasterNode, common.MemoryCacheEnabled = false, false, false
	common.SQLitePath = filepath.Join(t.TempDir(), "team.db")
	require.NoError(t, model.InitDB())
	common.SQLitePath = filepath.Join(t.TempDir(), "team-logs.db")
	require.NoError(t, model.InitLogDB())
	mainSQL, err := model.DB.DB()
	require.NoError(t, err)
	logSQL, err := model.LOG_DB.DB()
	require.NoError(t, err)
	mainSQL.SetMaxOpenConns(1)
	logSQL.SetMaxOpenConns(1)
	common.IsMasterNode = true
	t.Cleanup(func() {
		assert.NoError(t, mainSQL.Close())
		assert.NoError(t, logSQL.Close())
		model.DB, model.LOG_DB = previousDB, previousLogDB
		common.SetDatabaseTypes(previousMain, previousLog)
		common.RedisEnabled, common.IsMasterNode = previousRedis, previousMaster
		common.SQLitePath, common.MemoryCacheEnabled = previousSQLite, previousMemory
	})
	for range 2 {
		require.NoError(t, model.DB.AutoMigrate(&model.SubscriptionPlan{}, &model.UserSubscription{}, &model.SubscriptionPreConsumeRecord{}))
		require.NoError(t, model.DB.AutoMigrate(&model.WorkspaceMemberWeeklyUsage{}))
		require.NoError(t, model.DB.AutoMigrate(&model.WelcomePolicy{}, &model.WelcomeCredit{}, &model.WelcomeBrowser{}))
		require.NoError(t, model.InitializeWelcomePolicy())
		require.NoError(t, model.DB.AutoMigrate(&model.User{}, &model.Token{}, &model.UserSession{}, &model.AuthzRole{}, &model.CasbinRule{}, &model.WorkspaceTeam{}, &model.WorkspaceTeamAccount{}, &model.WorkspaceFundingMigration{}, &model.WorkspaceMember{}, &model.WorkspaceInvite{}, &model.WorkspacePersonalKey{}))
		require.NoError(t, model.LOG_DB.AutoMigrate(&model.Log{}, &model.AuditLog{}))
		require.NoError(t, authz.Init(model.DB))
	}
	var count int64
	require.NoError(t, model.DB.Model(&model.User{}).Count(&count).Error)
	require.Zero(t, count, "TEAM_TEST_DSN must reference an empty disposable database")
}

func TestNicknameAndLogPrivacy(t *testing.T) {
	setupTeamDatabase(t)
	registerEnabled, passwordEnabled, emailEnabled := common.RegisterEnabled, common.PasswordRegisterEnabled, common.EmailVerificationEnabled
	consumeEnabled, exportEnabled := common.LogConsumeEnabled, common.DataExportEnabled
	common.RegisterEnabled, common.PasswordRegisterEnabled, common.EmailVerificationEnabled = true, true, false
	common.LogConsumeEnabled, common.DataExportEnabled = true, false
	t.Cleanup(func() {
		common.RegisterEnabled, common.PasswordRegisterEnabled, common.EmailVerificationEnabled = registerEnabled, passwordEnabled, emailEnabled
		common.LogConsumeEnabled, common.DataExportEnabled = consumeEnabled, exportEnabled
	})
	r := gin.New()
	r.POST("/register", Register)
	r.PUT("/self", middleware.UserAuth(), WorkspaceSessionRequired, UpdateSelf)
	r.GET("/self", middleware.UserAuth(), GetSelf)
	r.PUT("/setting", middleware.UserAuth(), WorkspaceSessionRequired, UpdateUserSetting)
	r.PATCH("/members/:id", middleware.UserAuth(), WorkspaceSessionRequired, UpdateWorkspaceMember)
	call := func(method, path, credential, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		if credential != "" {
			req.Header.Set("Authorization", "Bearer "+credential)
		}
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	users := make([]model.User, 3)
	sessions := make([]string, 3)
	for i, name := range []string{"  小韩  ", "", "Other owner"} {
		body, err := common.Marshal(map[string]any{"username": fmt.Sprintf("nickname%d", i), "display_name": name, "password": "Test-only-password-894!", "role": 100})
		require.NoError(t, err)
		res := call("POST", "/register", "", string(body))
		require.Contains(t, res.Body.String(), `"success":true`)
		require.NoError(t, model.DB.First(&users[i], "username = ?", fmt.Sprintf("nickname%d", i)).Error)
		assert.Equal(t, strings.TrimSpace(name), users[i].DisplayName)
		assert.Equal(t, common.RoleCommonUser, users[i].Role)
		session, err := service.CreateLoginSession(users[i].Id, "password", "127.0.0.1", "nickname-test")
		require.NoError(t, err)
		sessions[i] = session.AccessToken
	}
	res := call("POST", "/register", "", `{"username":"nickname-long","password":"Test-only-password-894!","display_name":"123456789012345678901"}`)
	assert.Contains(t, res.Body.String(), `"success":false`)
	_, err := model.CreateWorkspaceTeam(users[0].Id, "Nickname team")
	require.NoError(t, err)
	invite, _, err := model.CreateWorkspaceInvite(users[0].Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(users[1].Id, invite))
	_, err = model.CreateWorkspaceTeam(users[2].Id, "Other team")
	require.NoError(t, err)
	memberPath := fmt.Sprintf("/members/%d", users[1].Id)
	for _, credential := range []string{"", sessions[1], sessions[2]} {
		assert.Contains(t, call("PATCH", memberPath, credential, `{"display_name":"forbidden"}`).Body.String(), `"success":false`)
	}
	beforeToken, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	for _, nickname := range []string{"韩一岚", "", strings.Repeat("昵", 20)} {
		body, err := common.Marshal(map[string]string{"display_name": "  " + nickname + "  "})
		require.NoError(t, err)
		assert.Contains(t, call("PATCH", memberPath, sessions[0], string(body)).Body.String(), `"success":true`)
		var stored model.User
		require.NoError(t, model.DB.First(&stored, users[1].Id).Error)
		assert.Equal(t, nickname, stored.DisplayName)
		assert.Equal(t, users[1].Username, stored.Username)
		assert.Equal(t, users[1].Password, stored.Password)
		assert.Equal(t, users[1].AuthVersion, stored.AuthVersion)
		team, err := service.GetWorkspaceTeamView(users[1].Id, 0)
		require.NoError(t, err)
		for _, member := range team.Members {
			if member.UserID == users[1].Id {
				assert.Equal(t, nickname, member.DisplayName)
			}
		}
	}
	afterToken, err := model.GetWorkspaceKey(users[1].Id, false)
	require.NoError(t, err)
	assert.Equal(t, beforeToken, afterToken, "nickname edits cannot change billing limits, key, or access")
	for _, body := range []string{`{"display_name":null}`, `{"display_name":123}`, `{"display_name":"123456789012345678901","status":2}`} {
		assert.Contains(t, call("PATCH", memberPath, sessions[0], body).Body.String(), `"success":false`)
	}
	assert.Contains(t, call("PATCH", fmt.Sprintf("/members/%d", users[0].Id), sessions[0], `{"display_name":"团队负责人"}`).Body.String(), `"success":true`)
	assert.Equal(t, 401, call("PUT", "/self", "", `{"display_name":"no"}`).Code)
	for _, body := range []string{`{"display_name":" 本人昵称 "}`, `{"display_name":""}`} {
		assert.Contains(t, call("PUT", "/self", sessions[1], body).Body.String(), `"success":true`)
	}
	for _, body := range []string{`{"display_name":null}`, `{"display_name":123}`, `{"display_name":"123456789012345678901"}`} {
		assert.Contains(t, call("PUT", "/self", sessions[1], body).Body.String(), `"success":false`)
	}
	var member model.User
	require.NoError(t, model.DB.First(&member, users[1].Id).Error)
	assert.Empty(t, member.DisplayName)
	assert.Equal(t, users[1].Username, member.Username)
	assert.Equal(t, users[1].Password, member.Password)
	assert.Contains(t, call("GET", "/self", sessions[1], "").Body.String(), `"display_name":""`)
	// Legacy stored settings and legacy clients can no longer turn IP logging on.
	require.NoError(t, model.DB.Model(&member).Update("setting", `{"record_ip_log":true}`).Error)
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest("POST", "/v1/responses", nil)
	c.Request.RemoteAddr = "203.0.113.42:1234"
	model.RecordConsumeLog(c, member.Id, model.RecordConsumeLogParams{ModelName: "test-model", Quota: 7})
	model.RecordErrorLog(c, member.Id, 0, "test-model", "", "test error", 0, 0, false, "default", nil)
	var logs []model.Log
	require.NoError(t, model.LOG_DB.Where("user_id = ? AND type IN ?", member.Id, []int{model.LogTypeConsume, model.LogTypeError}).Find(&logs).Error)
	require.Len(t, logs, 2)
	for _, log := range logs {
		assert.Empty(t, log.Ip)
	}
	assert.Contains(t, call("PUT", "/setting", sessions[1], `{"notify_type":"email","quota_warning_threshold":100,"record_ip_log":true}`).Body.String(), `"success":true`)
	require.NoError(t, model.DB.First(&member, member.Id).Error)
	assert.NotContains(t, member.Setting, "record_ip_log")
}

func TestTeamMVP(t *testing.T) {
	setupTeamDatabase(t)
	adminPAT := "team-admin-pat"
	admin := model.User{Username: "team-admin", Role: common.RoleAdminUser, Status: common.UserStatusEnabled, AccessToken: &adminPAT, AffCode: "team-admin"}
	require.NoError(t, model.DB.Create(&admin).Error)
	commonPAT := "team-common-pat"
	member := model.User{Username: "team-existing", Role: common.RoleCommonUser, Status: common.UserStatusEnabled, AccessToken: &commonPAT, AffCode: "team-existing"}
	require.NoError(t, model.DB.Create(&member).Error)
	login, err := service.CreateLoginSession(admin.Id, "password", "127.0.0.1", "team-test")
	require.NoError(t, err)
	router := gin.New()
	router.Use(middleware.RequestId())
	team := router.Group("/api/team", middleware.DisableCache(), middleware.AdminAuth())
	team.GET("/overview", middleware.RequirePermission(authz.TeamRead), GetTeamOverview)
	team.POST("/members", middleware.RequirePermission(authz.TeamWrite), CreateTeamMember)
	router.GET("/relay-key", middleware.TokenAuth(), func(c *gin.Context) { c.JSON(200, gin.H{"user_id": c.GetInt("id")}) })
	call := func(method, path, token, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		return res
	}
	for _, token := range []string{"", "invalid", commonPAT} {
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			path := "/api/team/overview"
			if method == http.MethodPost {
				path = "/api/team/members"
			}
			assert.Contains(t, []int{401, 403}, call(method, path, token, `{}`).Code)
		}
	}
	require.NoError(t, model.DB.Model(&admin).Update("status", common.UserStatusDisabled).Error)
	for _, token := range []string{adminPAT, login.AccessToken} {
		assert.Equal(t, 401, call("GET", "/api/team/overview", token, "").Code)
		assert.Equal(t, 401, call("POST", "/api/team/members", token, `{}`).Code)
	}
	require.NoError(t, model.DB.Model(&admin).Update("status", common.UserStatusEnabled).Error)
	require.NoError(t, authz.SetUserPermissions(admin.Id, authz.PermissionsMap{authz.ResourceTeam: {authz.ActionRead: false, authz.ActionWrite: false}}))
	for _, token := range []string{adminPAT, login.AccessToken} {
		assert.Equal(t, 403, call("GET", "/api/team/overview", token, "").Code)
		assert.Equal(t, 403, call("POST", "/api/team/members", token, `{}`).Code)
	}
	require.NoError(t, authz.SetUserPermissions(admin.Id, authz.PermissionsMap{authz.ResourceTeam: {authz.ActionRead: true, authz.ActionWrite: true}}))
	for _, body := range []string{`{}`, `{"username":"bad name","quota":10}`, `{"username":"team-new","quota":0}`, `{"username":"team-new","quota":2000000001}`, `{"username":"team-new","quota":10,"models":["bad,model"]}`} {
		assert.Contains(t, call("POST", "/api/team/members", login.AccessToken, body).Body.String(), `"success":false`)
	}
	body := `{"username":"team-new","display_name":"New member","quota":500000,"models":["gpt-image-2","gpt-6-luna","gpt-image-2"],"role":100}`
	response := call("POST", "/api/team/members", login.AccessToken, body)
	require.Equal(t, 200, response.Code)
	assert.Contains(t, response.Header().Get("Cache-Control"), "no-store")
	var created struct {
		Success bool
		Data    service.TeamMemberCredentials
	}
	require.NoError(t, common.Unmarshal(response.Body.Bytes(), &created))
	require.True(t, created.Success, response.Body.String())
	assert.NotContains(t, response.Body.String(), `"password"`)
	assert.True(t, strings.HasPrefix(created.Data.APIKey, "sk-"))
	var stored model.User
	require.NoError(t, model.DB.First(&stored, created.Data.User.Id).Error)
	assert.Equal(t, common.RoleCommonUser, stored.Role, "extra request fields cannot escalate roles")
	assert.Equal(t, 500000, stored.Quota)
	assert.Equal(t, "wallet_only", stored.GetSetting().BillingPreference)
	assert.Empty(t, stored.Password, "API-only onboarding must not issue a permanent initial password")
	invalidLogin := model.User{Username: stored.Username, Password: "anything"}
	assert.ErrorIs(t, invalidLogin.ValidateAndFill(), model.ErrInvalidCredentials)
	var key model.Token
	require.NoError(t, model.DB.First(&key, created.Data.TokenId).Error)
	assert.Equal(t, stored.Id, key.UserId)
	assert.True(t, key.UnlimitedQuota, "all member keys share the same native wallet")
	assert.True(t, key.ModelLimitsEnabled)
	assert.Equal(t, "gpt-image-2,gpt-6-luna", key.ModelLimits)
	assert.Equal(t, 200, call("GET", "/relay-key", created.Data.APIKey, "").Code)
	assert.Equal(t, 401, call("GET", "/api/team/overview", created.Data.APIKey, "").Code)
	assert.Contains(t, call("POST", "/api/team/members", login.AccessToken, body).Body.String(), `"success":false`)
	rollback := model.User{Username: "team-rollback", Role: common.RoleCommonUser, Quota: 100000, AffCode: "team-rollback"}
	require.Error(t, model.CreateTeamMember(&rollback, &model.Token{Key: key.Key}))
	var count int64
	require.NoError(t, model.DB.Model(&model.User{}).Where("username = ?", rollback.Username).Count(&count).Error)
	assert.Zero(t, count, "a failed key must roll back its allocated wallet and user")
	for _, entry := range []model.Log{
		{UserId: member.Id, Type: model.LogTypeConsume, CreatedAt: 100, Quota: 10, PromptTokens: 20, CompletionTokens: 3},
		{UserId: stored.Id, Type: model.LogTypeConsume, CreatedAt: 101, Quota: 30, PromptTokens: 40, CompletionTokens: 5},
		{UserId: stored.Id, Type: model.LogTypeManage, CreatedAt: 101, Quota: 500},
		{UserId: stored.Id, Type: model.LogTypeConsume, CreatedAt: 200, Quota: 90},
		{UserId: admin.Id, Type: model.LogTypeConsume, CreatedAt: 101, Quota: 99},
	} {
		require.NoError(t, model.LOG_DB.Create(&entry).Error)
	}
	usage := call("GET", "/api/team/overview?start_timestamp=100&end_timestamp=200", adminPAT, "")
	var overview struct {
		Success bool
		Data    model.TeamOverview
	}
	require.NoError(t, common.Unmarshal(usage.Body.Bytes(), &overview))
	require.True(t, overview.Success)
	require.Len(t, overview.Data.Members, 2)
	assert.Equal(t, stored.Id, overview.Data.Members[0].Id)
	assert.EqualValues(t, 101, overview.Data.Members[0].LastRequestAt)
	assert.Equal(t, model.TeamTotals{Quota: 40, Requests: 2, PromptTokens: 60, CompletionTokens: 8}, overview.Data.Totals)
	for _, query := range []string{"start_timestamp=-1", "start_timestamp=2&end_timestamp=1", "start_timestamp=0&end_timestamp=999999999"} {
		assert.Contains(t, call("GET", "/api/team/overview?"+query, adminPAT, "").Body.String(), `"success":false`)
	}
	var audits []model.AuditLog
	require.NoError(t, model.LOG_DB.Where("action = ?", "user.create").Find(&audits).Error)
	require.Len(t, audits, 1)
	assert.Equal(t, admin.Id, audits[0].UserId)
	encoded, err := common.Marshal(audits)
	require.NoError(t, err)
	for _, secret := range []string{adminPAT, commonPAT, login.AccessToken, created.Data.APIKey, key.Key} {
		assert.NotContains(t, string(encoded), secret)
		assert.NotContains(t, usage.Body.String(), secret)
	}
	assert.Contains(t, string(encoded), fmt.Sprintf(`"target_user_id":%d`, stored.Id))
}

// Disabling enrollment must reject the server action as well as hide the UI.
func TestWorkspaceDisabledAccountVerification(t *testing.T) {
	oldEmail, oldTwoFA, oldRegistration := common.EmailBindingEnabled, common.TwoFAEnrollmentEnabled, common.EmailVerificationEnabled
	common.EmailBindingEnabled, common.TwoFAEnrollmentEnabled, common.EmailVerificationEnabled = false, false, false
	t.Cleanup(func() {
		common.EmailBindingEnabled, common.TwoFAEnrollmentEnabled, common.EmailVerificationEnabled = oldEmail, oldTwoFA, oldRegistration
	})
	for _, handler := range []gin.HandlerFunc{Setup2FA, Enable2FA, EmailBindStart, EmailBindResend, EmailBind, SendEmailVerification} {
		response := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(response)
		c.Request = httptest.NewRequest(http.MethodPost, "/", strings.NewReader("{}"))
		handler(c)
		var result map[string]any
		require.NoError(t, common.Unmarshal(response.Body.Bytes(), &result))
		assert.Equal(t, false, result["success"])
	}
}
