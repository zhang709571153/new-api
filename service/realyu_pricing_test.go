package service

import (
	"math"
	"os"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/pkg/billingexpr"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/stretchr/testify/require"
)

func TestCodexSettlementUsesDeliveredServiceTier(t *testing.T) {
	for _, tc := range []struct {
		name, requested, served string
		codex                   bool
		want                    int
	}{
		{"priority_downgraded", "priority", "default", true, 500},
		{"priority_delivered", "priority", "priority", true, 1000},
		{"tier_missing_no_surcharge", "priority", "", true, 500},
		{"unknown_tier_no_surcharge", "priority", "unknown", true, 500},
		{"upstream_priority", "default", "priority", true, 1000},
		{"other_providers_keep_contract", "priority", "default", false, 1000},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body, err := common.Marshal(map[string]string{"service_tier": tc.requested})
			require.NoError(t, err)
			originalBody := string(body)
			info := makeRelayInfo(`param("service_tier") == "priority" ? p * 2 : p`, 1, 1000, 0)
			info.ChannelMeta = &relaycommon.ChannelMeta{}
			if tc.codex {
				info.ChannelType = constant.ChannelTypeCodex
			}
			info.BillingRequestInput = &billingexpr.RequestInput{Body: body}
			info.StreamStatus = relaycommon.NewStreamStatus()
			var response dto.OpenAIResponsesResponse
			responseJSON, err := common.Marshal(map[string]any{"service_tier": tc.served, "usage": map[string]int{"input_tokens": 1000, "output_tokens": 0}})
			require.NoError(t, err)
			require.NoError(t, common.Unmarshal(responseJSON, &response))
			if tc.served == "" {
				response.ServiceTier = nil
			}
			accumulator := NewResponsesUsageAccumulator(info)
			accumulator.Observe(&dto.ResponsesStreamResponse{Type: "response.completed", Response: &response})
			usage := accumulator.Finish()
			ok, quota, result := TryTieredSettle(info, BuildTieredTokenParams(usage, false, nil))
			require.True(t, ok)
			require.NotNil(t, result)
			require.Equal(t, tc.want, quota)
			require.Equal(t, originalBody, string(info.BillingRequestInput.Body), "original request must remain immutable")
		})
	}
}

// Exercise the stored September 26 pricing fixture through the normal usage
// normalization and settlement engine, including cache and context boundaries.
func TestRealyuOfficialPricingSnapshot(t *testing.T) {
	var manifest struct {
		Discount float64 `json:"discount"`
		Models   map[string]struct {
			Input  float64  `json:"input"`
			Cached float64  `json:"cached_input"`
			Write  *float64 `json:"cache_write"`
			Output float64  `json:"output"`
			Fast   float64  `json:"fast_multiplier"`
			Expr   string   `json:"billing_expr"`
		} `json:"models"`
	}
	data, err := os.ReadFile("../lab/realyu-pricing-20260924.json")
	require.NoError(t, err)
	require.NoError(t, common.Unmarshal(data, &manifest))
	require.Equal(t, 0.1, manifest.Discount)
	require.Len(t, manifest.Models, 7)
	for name, rates := range manifest.Models {
		t.Run(name, func(t *testing.T) {
			for _, length := range []int{0, 1000, 272000, 272001, 500000} {
				for _, speed := range []string{"default", "fast", "priority"} {
					cached, writes, output := 0, 0, 0
					if length > 0 {
						cached, output = length/2, 500
						if rates.Write != nil {
							writes = 100
						}
					}
					usage := &dto.Usage{PromptTokens: length, CompletionTokens: output,
						PromptTokensDetails:    dto.InputTokenDetails{CachedTokens: cached, CacheWriteTokens: writes},
						CompletionTokenDetails: dto.OutputTokenDetails{ReasoningTokens: 100}}
					params := BuildTieredTokenParams(usage, false, billingexpr.UsedVarsByHash(rates.Expr, billingexpr.ExprHashString(rates.Expr)))
					body, err := common.Marshal(map[string]string{"service_tier": speed})
					require.NoError(t, err)
					cost, _, err := billingexpr.RunExprWithRequest(rates.Expr, params, billingexpr.RequestInput{Body: body})
					require.NoError(t, err)
					inputCost := float64(length-cached-writes)*rates.Input + float64(cached)*rates.Cached
					if rates.Write != nil {
						inputCost += float64(writes) * *rates.Write
					}
					outputCost := float64(output) * rates.Output
					if length > 272000 {
						inputCost *= 2
						outputCost *= 1.5
					}
					want := inputCost + outputCost
					if speed != "default" {
						want *= rates.Fast
					}
					require.InDelta(t, want, cost, math.Max(1e-8, want*1e-12), "%s %d", speed, length)
					quota, err := billingexpr.QuotaRoundStrict(cost / 1_000_000 * 500000 * manifest.Discount)
					require.NoError(t, err)
					require.Equal(t, int(math.Round(want/20)), quota)
				}
			}
		})
	}
}
