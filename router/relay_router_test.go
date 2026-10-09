package router

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestListModelsSupportsOpenAIAndGeminiAuthentication(t *testing.T) {
	setupRelayRouterTestDB(t)

	user := model.User{
		Username: "models-user",
		Status:   common.UserStatusEnabled,
		Group:    "default",
		Quota:    100,
	}
	require.NoError(t, model.DB.Create(&user).Error)
	require.NoError(t, model.DB.Create(&model.Token{
		UserId:         user.Id,
		Key:            "modelstestkey",
		Status:         common.TokenStatusEnabled,
		ExpiredTime:    -1,
		UnlimitedQuota: true,
	}).Error)

	engine := gin.New()
	SetRelayRouter(engine)

	tests := []struct {
		name           string
		path           string
		headerName     string
		expectedObject string
		expectedField  string
	}{
		{
			name:           "OpenAI bearer token",
			path:           "/v1/models",
			headerName:     "Authorization",
			expectedObject: "list",
			expectedField:  "data",
		},
		{
			name:          "Gemini API key header",
			path:          "/v1/models",
			headerName:    "x-goog-api-key",
			expectedField: "models",
		},
		{
			name:          "Gemini API key query",
			path:          "/v1/models?key=modelstestkey",
			expectedField: "models",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodGet, test.path, nil)
			if test.headerName != "" {
				value := "modelstestkey"
				if test.headerName == "Authorization" {
					value = "Bearer " + value
				}
				request.Header.Set(test.headerName, value)
			}

			engine.ServeHTTP(recorder, request)

			require.Equal(t, http.StatusOK, recorder.Code)
			var payload map[string]any
			require.NoError(t, common.Unmarshal(recorder.Body.Bytes(), &payload))
			assert.Contains(t, payload, test.expectedField)
			assert.NotContains(t, payload, "error")
			if test.expectedObject != "" {
				assert.Equal(t, test.expectedObject, payload["object"])
			}
		})
	}
}

func TestRetrieveModelUsesAuthorizedLiveCatalog(t *testing.T) {
	setupRelayRouterTestDB(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Channel{}, &model.Model{}, &model.Vendor{}))
	originalSelfUse := operation_setting.SelfUseModeEnabled
	operation_setting.SelfUseModeEnabled = true
	t.Cleanup(func() { operation_setting.SelfUseModeEnabled = originalSelfUse })
	t.Setenv("REALYU_UPSTREAM_DRIVER", "sub2api")
	user := model.User{Username: "model-detail-user", Status: common.UserStatusEnabled, Group: "default", Quota: 0}
	require.NoError(t, model.DB.Create(&user).Error)
	// Discovery does not spend money or require a positive inference balance.
	token := model.Token{UserId: user.Id, Key: "modeldetailkey", Status: common.TokenStatusEnabled, ExpiredTime: -1, RemainQuota: 0, ModelLimitsEnabled: true, ModelLimits: "gpt-6.1-sol,vip-only-model"}
	require.NoError(t, model.DB.Create(&token).Error)
	channel := model.Channel{Type: constant.ChannelTypeSub2API, Name: "detail-live-route", Status: common.ChannelStatusEnabled, Key: "unused", Models: "gpt-6.1-sol,gpt-4,vip-only-model", Group: "default,vip"}
	require.NoError(t, model.DB.Create(&channel).Error)
	require.NoError(t, model.DB.Create(&[]model.Ability{
		{Group: "default", Model: "gpt-6.1-sol", ChannelId: channel.Id, Enabled: true},
		{Group: "default", Model: "gpt-4", ChannelId: channel.Id, Enabled: true},
		{Group: "vip", Model: "vip-only-model", ChannelId: channel.Id, Enabled: true},
	}).Error)
	engine := gin.New()
	SetRelayRouter(engine)
	request := func(path string) *httptest.ResponseRecorder {
		recorder := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("Authorization", "Bearer "+token.Key)
		engine.ServeHTTP(recorder, req)
		return recorder
	}
	listed := request("/v1/models")
	require.Equal(t, http.StatusOK, listed.Code)
	var catalog struct {
		Data []map[string]any `json:"data"`
	}
	require.NoError(t, common.Unmarshal(listed.Body.Bytes(), &catalog))
	require.Len(t, catalog.Data, 1)
	for _, tc := range []struct {
		name, id string
		status   int
	}{
		{"permitted custom model", "gpt-6.1-sol", http.StatusOK},
		{"token model denied even if built in", "gpt-4", http.StatusNotFound},
		{"group denied even if token names model", "vip-only-model", http.StatusNotFound},
		{"unknown model", "not-a-real-model", http.StatusNotFound},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := request("/v1/models/" + tc.id)
			require.Equal(t, tc.status, got.Code, got.Body.String())
			var body map[string]any
			require.NoError(t, common.Unmarshal(got.Body.Bytes(), &body))
			if tc.status == http.StatusOK {
				assert.Equal(t, catalog.Data[0], body, "detail must match the authorized list object")
				assert.Equal(t, tc.id, body["id"])
				assert.Equal(t, "model", body["object"])
				assert.NotContains(t, body, "error")
			} else {
				errorBody, ok := body["error"].(map[string]any)
				require.True(t, ok)
				assert.Equal(t, "model_not_found", errorBody["code"])
				assert.Equal(t, "invalid_request_error", errorBody["type"])
				assert.Equal(t, "model", errorBody["param"])
			}
		})
	}
	var afterUser model.User
	var afterToken model.Token
	require.NoError(t, model.DB.First(&afterUser, user.Id).Error)
	require.NoError(t, model.DB.First(&afterToken, token.Id).Error)
	assert.Zero(t, afterUser.UsedQuota)
	assert.Zero(t, afterToken.UsedQuota)
	assert.Zero(t, afterToken.RemainQuota)
	assert.Equal(t, common.TokenStatusEnabled, afterToken.Status)
}

func setupRelayRouterTestDB(t *testing.T) {
	t.Helper()

	gin.SetMode(gin.TestMode)
	originalIsMasterNode := common.IsMasterNode
	originalRedisEnabled := common.RedisEnabled
	originalSQLitePath := common.SQLitePath
	originalMainDatabaseType := common.MainDatabaseType()
	originalLogDatabaseType := common.LogDatabaseType()
	originalSQLDSN, hadSQLDSN := os.LookupEnv("SQL_DSN")

	common.IsMasterNode = false
	common.RedisEnabled = false
	common.SQLitePath = fmt.Sprintf("file:%s?mode=memory&cache=shared", strings.ReplaceAll(t.Name(), "/", "_"))
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	require.NoError(t, os.Setenv("SQL_DSN", "local"))
	require.NoError(t, model.InitDB())
	model.LOG_DB = model.DB
	require.NoError(t, model.DB.AutoMigrate(&model.User{}, &model.Token{}, &model.Ability{}))

	t.Cleanup(func() {
		if sqlDB, err := model.DB.DB(); err == nil {
			_ = sqlDB.Close()
		}
		common.IsMasterNode = originalIsMasterNode
		common.RedisEnabled = originalRedisEnabled
		common.SQLitePath = originalSQLitePath
		common.SetDatabaseTypes(originalMainDatabaseType, originalLogDatabaseType)
		if hadSQLDSN {
			require.NoError(t, os.Setenv("SQL_DSN", originalSQLDSN))
		} else {
			require.NoError(t, os.Unsetenv("SQL_DSN"))
		}
	})
}
