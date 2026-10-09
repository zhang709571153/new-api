package controller

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestClientModelDiscoveryAuthorization(t *testing.T) {
	owner, member, team, teamKey := setupFundingIsolation(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Channel{}, &model.Ability{}, &model.Model{}, &model.Vendor{}))
	previousSelfUse := operation_setting.SelfUseModeEnabled
	operation_setting.SelfUseModeEnabled = true
	t.Cleanup(func() { operation_setting.SelfUseModeEnabled = previousSelfUse })
	require.NoError(t, model.DB.Create(&model.Channel{Id: 810, Name: "local-fixture", Type: constant.ChannelTypeOpenAI, Key: "not-an-upstream-key", Group: "default", Status: common.ChannelStatusEnabled, Models: "gpt-6-luna,gpt-6-sol"}).Error)
	require.NoError(t, model.DB.Create(&[]model.Ability{
		{Group: "default", Model: "gpt-6-luna", ChannelId: 810, Enabled: true},
		{Group: "default", Model: "gpt-6-sol", ChannelId: 810, Enabled: true},
		{Group: "discovery-private", Model: "gpt-5.5", ChannelId: 810, Enabled: true},
	}).Error)
	personalKey, err := model.GetWorkspaceKeyForScope(member.Id, "personal", true)
	require.NoError(t, err)
	for _, key := range []*model.Token{personalKey, teamKey} {
		require.NoError(t, model.DB.Model(key).Updates(map[string]any{"remain_quota": 0, "unlimited_quota": false}).Error)
	}
	r := gin.New()
	require.NoError(t, r.SetTrustedProxies(nil))
	r.GET("/v1/models", middleware.TokenAuthModelList(), func(c *gin.Context) { ListModels(c, constant.ChannelTypeOpenAI) })
	for _, path := range []string{"/v1/models/:model", "/v1/responses/:response_id", "/v1beta/models"} {
		r.GET(path, middleware.TokenAuth(), func(c *gin.Context) { c.Status(200) })
	}
	r.POST("/v1/models", middleware.TokenAuth(), func(c *gin.Context) { c.Status(200) })
	request := func(method, path string, key *model.Token) *httptest.ResponseRecorder {
		t.Helper()
		req := httptest.NewRequest(method, path, nil)
		req.RemoteAddr = "127.0.0.1:12345"
		req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(key))
		res := httptest.NewRecorder()
		r.ServeHTTP(res, req)
		return res
	}
	assertModels := func(t *testing.T, key *model.Token, expected ...string) {
		t.Helper()
		res := request(http.MethodGet, "/v1/models", key)
		require.Equal(t, http.StatusOK, res.Code)
		var payload listModelsResponse
		require.NoError(t, common.Unmarshal(res.Body.Bytes(), &payload))
		ids := make([]string, 0, len(payload.Data))
		for _, item := range payload.Data {
			ids = append(ids, item.Id)
		}
		assert.ElementsMatch(t, expected, ids)
	}
	for _, entry := range []struct {
		name string
		key  *model.Token
	}{{"personal", personalKey}, {"team", teamKey}} {
		t.Run(entry.name+" zero and historical exhausted", func(t *testing.T) {
			assertModels(t, entry.key, "gpt-6-luna", "gpt-6-sol")
			require.NoError(t, model.DB.Model(entry.key).Update("status", common.TokenStatusExhausted).Error)
			assertModels(t, entry.key, "gpt-6-luna", "gpt-6-sol")
			var stored model.Token
			require.NoError(t, model.DB.First(&stored, entry.key.Id).Error)
			assert.Equal(t, common.TokenStatusExhausted, stored.Status, "discovery cannot restore spending permission")
			assert.Zero(t, stored.RemainQuota)
			wantStatus := http.StatusUnauthorized
			if entry.name == "team" {
				wantStatus = http.StatusForbidden // the independent team funding gate rejects spending
			}
			assert.Equal(t, wantStatus, fundingRequest(t, entry.key, entry.name+"-empty-model-discovery", 1, 1).Code)
			require.NoError(t, model.DB.Model(entry.key).Update("status", common.TokenStatusEnabled).Error)
		})
	}
	t.Run("identity authentication does not grant spending or retained-response access", func(t *testing.T) {
		for _, path := range []string{"/v1/models/gpt-6-luna", "/v1beta/models"} {
			assert.Equal(t, 200, request(http.MethodGet, path, teamKey).Code)
		}
		assert.Equal(t, 200, request(http.MethodPost, "/v1/models", teamKey).Code)
		assert.Equal(t, 403, request(http.MethodGet, "/v1/responses/fixture", teamKey).Code)
		assert.Equal(t, 403, fundingRequest(t, teamKey, "discovery-identity-cannot-spend", 1, 1).Code)
		require.NoError(t, model.DB.First(&owner, owner.Id).Error)
		require.NoError(t, model.DB.First(&member, member.Id).Error)
		assert.Equal(t, 1000, owner.Quota)
		assert.Equal(t, 2000, member.Quota)
	})
	t.Run("discovery middleware refuses accidental route reuse", func(t *testing.T) {
		guarded := gin.New()
		invoked := false
		end := func(c *gin.Context) { invoked = true; c.Status(200) }
		guarded.GET("/v1/responses", middleware.TokenAuthModelList(), end)
		guarded.POST("/v1/models", middleware.TokenAuthModelList(), end)
		for _, test := range []struct{ method, path string }{{http.MethodGet, "/v1/responses"}, {http.MethodPost, "/v1/models"}} {
			req := httptest.NewRequest(test.method, test.path, nil)
			req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(teamKey))
			res := httptest.NewRecorder()
			guarded.ServeHTTP(res, req)
			assert.Equal(t, 403, res.Code)
		}
		assert.False(t, invoked)
	})
	t.Run("key status and expiry", func(t *testing.T) {
		for _, key := range []*model.Token{personalKey, teamKey} {
			for _, status := range []int{common.TokenStatusDisabled, common.TokenStatusExpired} {
				require.NoError(t, model.DB.Model(key).Update("status", status).Error)
				assert.Equal(t, 401, request(http.MethodGet, "/v1/models", key).Code)
			}
			require.NoError(t, model.DB.Model(key).Updates(map[string]any{"status": common.TokenStatusEnabled, "expired_time": common.GetTimestamp() - 10}).Error)
			assert.Equal(t, 401, request(http.MethodGet, "/v1/models", key).Code)
			require.NoError(t, model.DB.Model(key).Update("expired_time", -1).Error)
		}
		assert.Equal(t, 401, request(http.MethodGet, "/v1/models", &model.Token{Key: "invalid"}).Code)
	})
	t.Run("IP and group and model restrictions", func(t *testing.T) {
		require.NoError(t, model.DB.Model(teamKey).Update("allow_ips", "192.0.2.0/24").Error)
		assert.Equal(t, 403, request(http.MethodGet, "/v1/models", teamKey).Code)
		require.NoError(t, model.DB.Model(teamKey).Update("allow_ips", "127.0.0.0/8").Error)
		assertModels(t, teamKey, "gpt-6-luna", "gpt-6-sol")
		require.NoError(t, model.DB.Model(teamKey).Update("group", "discovery-private").Error)
		assert.Equal(t, 403, request(http.MethodGet, "/v1/models", teamKey).Code)
		require.NoError(t, model.DB.Model(teamKey).Updates(map[string]any{"group": "", "allow_ips": "", "model_limits_enabled": true, "model_limits": "gpt-6-luna"}).Error)
		assertModels(t, teamKey, "gpt-6-luna")
		require.NoError(t, model.DB.Model(teamKey).Update("model_limits", "").Error)
		assertModels(t, teamKey)
		require.NoError(t, model.DB.Model(teamKey).Update("model_limits_enabled", false).Error)
	})
	t.Run("account and membership revocation", func(t *testing.T) {
		for _, userID := range []int{owner.Id, member.Id} {
			require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", userID).Update("status", common.UserStatusDisabled).Error)
			assert.Contains(t, []int{401, 403}, request(http.MethodGet, "/v1/models", teamKey).Code)
			if userID == member.Id {
				assert.Equal(t, 403, request(http.MethodGet, "/v1/models", personalKey).Code)
			}
			require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", userID).Update("status", common.UserStatusEnabled).Error)
		}
		require.NoError(t, model.DB.Model(&model.WorkspaceMember{}).Where("user_id = ?", member.Id).Update("status", common.UserStatusDisabled).Error)
		assert.Equal(t, 401, request(http.MethodGet, "/v1/models", teamKey).Code)
		require.NoError(t, model.DB.Model(&model.WorkspaceMember{}).Where("user_id = ?", member.Id).Update("status", common.UserStatusEnabled).Error)
		assertModels(t, teamKey, "gpt-6-luna", "gpt-6-sol")
	})
	t.Run("empty personal wallet can discover but cannot spend", func(t *testing.T) {
		require.NoError(t, model.DB.Model(&member).Update("quota", 0).Error)
		require.NoError(t, model.DB.Model(personalKey).Update("unlimited_quota", true).Error)
		assertModels(t, personalKey, "gpt-6-luna", "gpt-6-sol")
		assert.Equal(t, 403, fundingRequest(t, personalKey, "discovery-personal-wallet-empty", 1, 1).Code)
		require.NoError(t, model.DB.Model(&member).Update("quota", 2000).Error)
		require.NoError(t, model.DB.Model(personalKey).Update("unlimited_quota", false).Error)
	})
	t.Run("funding pools remain isolated", func(t *testing.T) {
		// A positive per-key limit cannot make an empty team funding pool spend,
		// even when both members' personal wallets have money.
		require.NoError(t, model.DB.Model(teamKey).Update("remain_quota", 10).Error)
		assertModels(t, teamKey, "gpt-6-luna", "gpt-6-sol")
		assert.Equal(t, 403, fundingRequest(t, teamKey, "discovery-team-empty", 1, 1).Code)
		require.NoError(t, model.DB.First(&owner, owner.Id).Error)
		require.NoError(t, model.DB.First(&member, member.Id).Error)
		assert.Equal(t, 1000, owner.Quota)
		assert.Equal(t, 2000, member.Quota)
	})
	t.Run("rotated key is rejected", func(t *testing.T) {
		oldKey := *teamKey
		require.NoError(t, model.RotateWorkspaceToken(teamKey))
		assert.Equal(t, 401, request(http.MethodGet, "/v1/models", &oldKey).Code)
		assertModels(t, teamKey, "gpt-6-luna", "gpt-6-sol")
	})
	t.Run("removed member cannot discover", func(t *testing.T) {
		require.NoError(t, model.RemoveWorkspaceMember(owner.Id, team.ID, member.Id))
		assert.Equal(t, 401, request(http.MethodGet, "/v1/models", teamKey).Code)
		assertModels(t, personalKey, "gpt-6-luna", "gpt-6-sol")
	})
}
