package codex

import (
	"encoding/json"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/samber/lo"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetRequestURLAlphaSearch(t *testing.T) {
	adaptor := &Adaptor{}
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:    constant.ChannelTypeCodex,
			ChannelBaseUrl: "https://chatgpt.com",
		},
		RelayMode: relayconstant.RelayModeAlphaSearch,
	}

	url, err := adaptor.GetRequestURL(info)
	require.NoError(t, err)
	assert.Equal(t, "https://chatgpt.com/backend-api/codex/alpha/search", url)
}

// The Codex backend rejects these fields, so the adaptor clears them rather
// than forwarding what the client sent.
func TestConvertOpenAIResponsesRequestDropsPenalties(t *testing.T) {
	adaptor := &Adaptor{}
	info := &relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{ChannelType: constant.ChannelTypeCodex},
		RelayMode:   relayconstant.RelayModeResponses,
	}

	converted, err := adaptor.ConvertOpenAIResponsesRequest(nil, info, dto.OpenAIResponsesRequest{
		Model:            "gpt-5-codex",
		Input:            json.RawMessage(`"hello"`),
		MaxOutputTokens:  lo.ToPtr(uint(128)),
		Temperature:      lo.ToPtr(1.0),
		FrequencyPenalty: json.RawMessage(`1.5`),
		PresencePenalty:  json.RawMessage(`1.5`),
	})
	require.NoError(t, err)

	request, ok := converted.(dto.OpenAIResponsesRequest)
	require.True(t, ok)
	assert.Nil(t, request.MaxOutputTokens)
	assert.Nil(t, request.Temperature)
	assert.Nil(t, request.FrequencyPenalty)
	assert.Nil(t, request.PresencePenalty)
}

func TestConvertOpenAIResponsesRequestPreservesImageAndToolInput(t *testing.T) {
	input := json.RawMessage(`[{"role":"system","content":[{"type":"input_text","text":"Keep instructions"}]},{"type":"function_call_output","call_id":"call_1","output":"0"},{"role":"user","content":[{"type":"input_image","image_url":"data:image/png;base64,eA=="}],"custom":0}]`)
	want := `[{"role":"developer","content":[{"type":"input_text","text":"Keep instructions"}]},{"type":"function_call_output","call_id":"call_1","output":"0"},{"role":"user","content":[{"type":"input_image","image_url":"data:image/png;base64,eA=="}],"custom":0}]`
	for _, mode := range []int{relayconstant.RelayModeResponses, relayconstant.RelayModeResponsesCompact} {
		info := &relaycommon.RelayInfo{RelayMode: mode, ChannelMeta: &relaycommon.ChannelMeta{}}
		converted, err := (&Adaptor{}).ConvertOpenAIResponsesRequest(nil, info, dto.OpenAIResponsesRequest{
			Input: input,
			Tools: json.RawMessage(`[{"type":"image_generation","model":"gpt-image-2","quality":"low"}]`),
		})
		require.NoError(t, err)
		request, ok := converted.(dto.OpenAIResponsesRequest)
		require.True(t, ok)
		assert.JSONEq(t, want, string(request.Input))
		tools, err := common.Marshal(request.Tools)
		require.NoError(t, err)
		assert.JSONEq(t, `[{"type":"image_generation","model":"gpt-image-2","quality":"low"}]`, string(tools))
	}
}

func TestConvertOpenAIResponsesRequestInputWithoutSystemRole(t *testing.T) {
	for _, input := range []string{`"hello"`, `[{"role":"developer","content":"keep"}]`, ` [ {"role":"user","content":"hello"} ] `} {
		converted, err := (&Adaptor{}).ConvertOpenAIResponsesRequest(nil, nil, dto.OpenAIResponsesRequest{Input: json.RawMessage(input)})
		require.NoError(t, err)
		request, ok := converted.(dto.OpenAIResponsesRequest)
		require.True(t, ok)
		assert.Equal(t, input, string(request.Input))
	}
	_, err := (&Adaptor{}).ConvertOpenAIResponsesRequest(nil, nil, dto.OpenAIResponsesRequest{Input: json.RawMessage(`[{"role":"system"`)})
	require.Error(t, err)
}
