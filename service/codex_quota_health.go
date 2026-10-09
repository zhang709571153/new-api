package service

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/gin-gonic/gin"
)

const CodexQuotaExhaustedReason = "codex_usage_limit_reached"
const codexQuotaRejectedKey = "codex_quota_rejected_before_output"
const codexQuotaDisabledKey = "codex_quota_disabled_channel"

// RecordCodexQuotaRejection is called only for an HTTP rejection, before the
// response body is streamed. A generic 429, timeout or interrupted image is not
// evidence that the account's subscription quota is exhausted.
func RecordCodexQuotaRejection(c *gin.Context, apiErr *types.NewAPIError, portable bool) {
	if c != nil {
		c.Set(codexQuotaRejectedKey, false)
		c.Set(codexQuotaDisabledKey, 0)
	}
	if c == nil || apiErr == nil || c.GetInt("channel_type") != constant.ChannelTypeCodex ||
		apiErr.StatusCode != http.StatusTooManyRequests ||
		(apiErr.ToOpenAIError().Type != "usage_limit_reached" && string(apiErr.GetErrorCode()) != "usage_limit_reached") ||
		!common.AutomaticDisableChannelEnabled || !c.GetBool("auto_ban") {
		return
	}
	id := c.GetInt("channel_id")
	_, err := model.TransitionChannelHealth(id, common.ChannelStatusEnabled, common.ChannelStatusAutoDisabled, "", CodexQuotaExhaustedReason)
	if err != nil {
		common.SysError(fmt.Sprintf("codex quota health update failed: channel=%d error=%v", id, err))
		return
	}
	// Concurrent rejected requests may already have disabled this account.
	ch, err := model.CacheGetChannel(id)
	if err == nil && ch.Status == common.ChannelStatusAutoDisabled && ch.GetOtherInfo()["status_reason"] == CodexQuotaExhaustedReason {
		c.Set(codexQuotaRejectedKey, portable)
		c.Set(codexQuotaDisabledKey, id)
	}
}

// RecoverCodexQuotaChannel checks subscription availability without generating
// billable model traffic. Unknown/malformed/failed probes leave it unavailable.
func RecoverCodexQuotaChannel(ctx context.Context, channel *model.Channel) (bool, error) {
	if channel == nil || channel.Type != constant.ChannelTypeCodex || channel.Status != common.ChannelStatusAutoDisabled ||
		channel.GetOtherInfo()["status_reason"] != CodexQuotaExhaustedReason || channel.ChannelInfo.IsMultiKey {
		return false, nil
	}
	key, err := parseCodexOAuthKey(channel.Key)
	if err != nil {
		return false, fmt.Errorf("invalid Codex credential")
	}
	client, err := GetHttpClientWithProxy(channel.GetSetting().Proxy)
	if err != nil {
		return false, err
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	status, body, err := FetchCodexWhamUsage(ctx, client, channel.GetBaseURL(), key.AccessToken, key.AccountID)
	if err != nil || status != http.StatusOK {
		return false, fmt.Errorf("Codex availability probe failed: status=%d", status)
	}
	var usage struct {
		RateLimit *struct {
			Allowed      *bool `json:"allowed"`
			LimitReached *bool `json:"limit_reached"`
		} `json:"rate_limit"`
	}
	if common.Unmarshal(body, &usage) != nil || usage.RateLimit == nil || usage.RateLimit.Allowed == nil || usage.RateLimit.LimitReached == nil {
		return false, fmt.Errorf("Codex availability probe returned incomplete data")
	}
	if !*usage.RateLimit.Allowed || *usage.RateLimit.LimitReached {
		return false, nil
	}
	return model.TransitionChannelHealth(channel.Id, common.ChannelStatusAutoDisabled, common.ChannelStatusEnabled, CodexQuotaExhaustedReason, "")
}
