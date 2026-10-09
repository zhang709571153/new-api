package controller

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCodexQuotaHealth(t *testing.T) {
	kind := os.Getenv("TEST_TASK_DB_DIALECT")
	if kind == "" {
		kind = "sqlite"
	}
	dsn := os.Getenv(map[string]string{"mysql": "TEST_MYSQL_DSN", "postgres": "TEST_POSTGRES_DSN"}[kind])
	db := modelManagementDB(t, kind, dsn)
	dialect := common.MainDatabaseType()
	oldDB, oldType := model.DB, common.MainDatabaseType()
	oldMemory, oldDisable, oldEnable := common.MemoryCacheEnabled, common.AutomaticDisableChannelEnabled, common.AutomaticEnableChannelEnabled
	model.DB = db
	common.SetMainDatabaseType(dialect)
	common.AutomaticDisableChannelEnabled, common.AutomaticEnableChannelEnabled = true, true
	t.Cleanup(func() {
		model.DB = oldDB
		common.SetMainDatabaseType(oldType)
		common.MemoryCacheEnabled, common.AutomaticDisableChannelEnabled, common.AutomaticEnableChannelEnabled = oldMemory, oldDisable, oldEnable
	})
	for _, cached := range []bool{false, true} {
		t.Run(map[bool]string{true: "cached", false: "database"}[cached], func(t *testing.T) {
			common.MemoryCacheEnabled = cached
			body, code := `{"rate_limit":{"allowed":false,"limit_reached":true}}`, 200
			probes := 0
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				probes++
				assert.Equal(t, "/backend-api/wham/usage", r.URL.Path)
				assert.Equal(t, http.MethodGet, r.Method)
				w.WriteHeader(code)
				_, _ = w.Write([]byte(body))
			}))
			defer upstream.Close()
			ch := &model.Channel{Type: constant.ChannelTypeCodex, Name: "quota-fixture", Key: `{"access_token":"test-only","account_id":"test-account"}`,
				Status: common.ChannelStatusEnabled, Group: "default", Models: "gpt-6-luna", BaseURL: &upstream.URL, AutoBan: common.GetPointer(1)}
			require.NoError(t, ch.Insert())
			fallback := &model.Channel{Type: constant.ChannelTypeCodex, Name: "healthy-fixture", Key: ch.Key,
				Status: common.ChannelStatusEnabled, Group: "default", Models: "gpt-6-luna"}
			require.NoError(t, fallback.Insert())
			model.InitChannelCache()
			c, _ := gin.CreateTestContext(httptest.NewRecorder())
			c.Request = httptest.NewRequest(http.MethodPost, "/v1/responses", nil)
			c.Set("channel_id", ch.Id)
			c.Set("channel_type", constant.ChannelTypeCodex)
			c.Set("auto_ban", true)
			quotaError := types.WithOpenAIError(types.OpenAIError{Type: "usage_limit_reached", Message: "The usage limit has been reached"}, 429)
			for _, err := range []*types.NewAPIError{
				types.WithOpenAIError(types.OpenAIError{Type: "rate_limit_error", Message: "slow down"}, 429),
				types.NewOpenAIError(errors.New("connection lost"), types.ErrorCodeDoRequestFailed, 500),
			} {
				service.RecordCodexQuotaRejection(c, err, true)
				assert.Equal(t, "stop", service.DecideRelayRetry(c, err, 2).Action)
				loaded, loadErr := model.GetChannelById(ch.Id, true)
				require.NoError(t, loadErr)
				assert.Equal(t, common.ChannelStatusEnabled, loaded.Status)
			}
			service.RecordCodexQuotaRejection(c, quotaError, true)
			assert.Equal(t, "retry", service.DecideRelayRetry(c, quotaError, 2).Action)
			assert.Equal(t, "stop", service.DecideRelayRetry(c, quotaError, 0).Action)
			assert.Equal(t, "stop", service.DecideRelayRetry(c, types.NewOpenAIError(errors.New("lost after submission"), types.ErrorCodeDoRequestFailed, 500), 2).Action)
			selected, _, err := service.CacheGetRandomSatisfiedChannel(&service.RetryParam{Ctx: c, TokenGroup: "default", ModelName: "gpt-6-luna", Retry: common.GetPointer(1)})
			require.NoError(t, err)
			require.NotNil(t, selected)
			assert.NotEqual(t, ch.Id, selected.Id)
			loaded, err := model.GetChannelById(ch.Id, true)
			require.NoError(t, err)
			assert.Equal(t, common.ChannelStatusAutoDisabled, loaded.Status)
			assert.Equal(t, service.CodexQuotaExhaustedReason, loaded.GetOtherInfo()["status_reason"])
			var ability model.Ability
			require.NoError(t, db.Where("channel_id = ?", ch.Id).First(&ability).Error)
			assert.False(t, ability.Enabled)
			for _, probe := range []struct {
				body   string
				status int
			}{
				{`{"rate_limit":{"allowed":false,"limit_reached":true}}`, 200},
				{`{"rate_limit":{"allowed":true}}`, 200},
				{`{}`, 200}, {`<html>unavailable</html>`, 503},
			} {
				body, code = probe.body, probe.status
				summary := testChannelForHealthCheck(context.Background(), loaded, 0, false, 1000)
				assert.Zero(t, summary.Enabled)
			}
			body, code = `{"rate_limit":{"allowed":true,"limit_reached":false}}`, 200
			summary := testChannelForHealthCheck(context.Background(), loaded, 0, false, 1000)
			assert.Equal(t, 1, summary.Enabled)
			assert.Equal(t, 5, probes, "recovery uses only availability probes, no inference")
			require.NoError(t, db.Where("channel_id = ?", ch.Id).First(&ability).Error)
			assert.True(t, ability.Enabled)
			service.RecordCodexQuotaRejection(c, quotaError, false)
			assert.Equal(t, "stop", service.DecideRelayRetry(c, quotaError, 2).Action, "upstream-only conversation state cannot be replayed on another account")
			// A stale probe cannot undo an administrator's later disable.
			require.True(t, model.UpdateChannelStatus(ch.Id, "", common.ChannelStatusManuallyDisabled, "operator"))
			recovered, err := service.RecoverCodexQuotaChannel(context.Background(), loaded)
			require.NoError(t, err)
			assert.False(t, recovered)
			service.RecordCodexQuotaRejection(c, quotaError, true)
			current, err := model.GetChannelById(ch.Id, true)
			require.NoError(t, err)
			assert.Equal(t, common.ChannelStatusManuallyDisabled, current.Status)
			service.RecordCodexQuotaRejection(c, quotaError, false)
			assert.Equal(t, "stop", service.DecideRelayRetry(c, quotaError, 2).Action, "upstream-only conversation state cannot be replayed on another account")
			c.Writer.WriteHeaderNow()
			assert.Equal(t, "stop", service.DecideRelayRetry(c, quotaError, 2).Action, "never replay after sending output")
		})
	}
}

func TestChannelHealthRecoveryIgnoresSuccessfulReasoningLatency(t *testing.T) {
	kind := os.Getenv("TEST_TASK_DB_DIALECT")
	if kind == "" {
		kind = "sqlite"
	}
	dsn := os.Getenv(map[string]string{"mysql": "TEST_MYSQL_DSN", "postgres": "TEST_POSTGRES_DSN"}[kind])
	db := modelManagementDB(t, kind, dsn)
	oldDisable, oldEnable := common.AutomaticDisableChannelEnabled, common.AutomaticEnableChannelEnabled
	common.AutomaticDisableChannelEnabled, common.AutomaticEnableChannelEnabled = true, true
	t.Cleanup(func() {
		common.AutomaticDisableChannelEnabled, common.AutomaticEnableChannelEnabled = oldDisable, oldEnable
	})

	for _, tc := range []struct {
		name         string
		channelType  int
		status       int
		allowDisable bool
		apiError     *types.NewAPIError
		localError   error
		wantStatus   int
		wantSummary  channelTestSummary
	}{
		{name: "Codex OAuth recovery after 5.5 seconds", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusAutoDisabled,
			wantStatus: common.ChannelStatusEnabled, wantSummary: channelTestSummary{Tested: 1, Succeeded: 1, Enabled: 1}},
		{name: "scheduled Codex recovery after 5.5 seconds", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusAutoDisabled, allowDisable: true,
			wantStatus: common.ChannelStatusEnabled, wantSummary: channelTestSummary{Tested: 1, Succeeded: 1, Enabled: 1}},
		{name: "healthy Codex remains enabled", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusEnabled, allowDisable: true,
			wantStatus: common.ChannelStatusEnabled, wantSummary: channelTestSummary{Tested: 1, Succeeded: 1}},
		{name: "passive recovery ignores other channel latency", channelType: constant.ChannelTypeOpenAI, status: common.ChannelStatusAutoDisabled,
			wantStatus: common.ChannelStatusEnabled, wantSummary: channelTestSummary{Tested: 1, Succeeded: 1, Enabled: 1}},
		{name: "other scheduled channel keeps latency policy", channelType: constant.ChannelTypeOpenAI, status: common.ChannelStatusAutoDisabled, allowDisable: true,
			wantStatus: common.ChannelStatusAutoDisabled, wantSummary: channelTestSummary{Tested: 1, Failed: 1}},
		{name: "revoked authorization remains disabled", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusAutoDisabled,
			apiError:   types.WithOpenAIError(types.OpenAIError{Type: "invalid_request_error", Code: "token_revoked", Message: "credential revoked"}, http.StatusUnauthorized),
			wantStatus: common.ChannelStatusAutoDisabled, wantSummary: channelTestSummary{Tested: 1, Failed: 1}},
		{name: "quota rejection remains disabled", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusAutoDisabled,
			apiError:   types.WithOpenAIError(types.OpenAIError{Type: "usage_limit_reached", Message: "quota exhausted"}, http.StatusTooManyRequests),
			wantStatus: common.ChannelStatusAutoDisabled, wantSummary: channelTestSummary{Tested: 1, Failed: 1}},
		{name: "local probe failure is not success", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusAutoDisabled, localError: errors.New("probe failed"),
			wantStatus: common.ChannelStatusAutoDisabled, wantSummary: channelTestSummary{Tested: 1, Failed: 1}},
		{name: "manual disable is preserved", channelType: constant.ChannelTypeCodex, status: common.ChannelStatusManuallyDisabled,
			wantStatus: common.ChannelStatusManuallyDisabled, wantSummary: channelTestSummary{Tested: 1, Succeeded: 1}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			channel := &model.Channel{Type: tc.channelType, Name: tc.name, Key: "test-only", Status: tc.status,
				Group: "default", Models: "gpt-6-luna", AutoBan: common.GetPointer(1)}
			require.NoError(t, channel.Insert())
			ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
			ctx.Request = httptest.NewRequest(http.MethodPost, "/v1/responses", nil)
			result := testResult{context: ctx, localErr: tc.localError, newAPIError: tc.apiError}
			// Feed the actual incident duration without sleeps or live requests.
			summary := applyChannelTestHealthResult(channel, result, tc.allowDisable, 5000, 5500)
			assert.Equal(t, tc.wantSummary, summary)
			loaded, err := model.GetChannelById(channel.Id, true)
			require.NoError(t, err)
			assert.Equal(t, tc.wantStatus, loaded.Status)
			assert.Equal(t, 5500, loaded.ResponseTime, "slow successful probes remain visible")
			var ability model.Ability
			require.NoError(t, db.Where("channel_id = ?", channel.Id).First(&ability).Error)
			assert.Equal(t, tc.wantStatus == common.ChannelStatusEnabled, ability.Enabled)
		})
	}
}
