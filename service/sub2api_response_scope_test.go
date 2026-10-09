package service

import (
	"os"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
)

func TestSub2APIResponseReferenceRejectsOtherMemberAndWorkspace(t *testing.T) {
	writeSub2APIConfig(t, "http://127.0.0.1:28090", nil)
	owner := Sub2APISubject{UserID: 68}
	wrapped, err := Sub2APIWrapResponseID(owner, "resp_upstream_retained")
	require.NoError(t, err)
	raw, err := Sub2APIUnwrapResponseID(owner, wrapped)
	require.NoError(t, err)
	require.Equal(t, "resp_upstream_retained", raw)
	for _, other := range []Sub2APISubject{{UserID: 67}, {UserID: 68, WorkspaceTeamID: 11}, {UserID: 68, WorkspaceTeamID: 12}, {}} {
		_, err = Sub2APIUnwrapResponseID(other, wrapped)
		require.ErrorIs(t, err, ErrSub2APIResponseNotOwned)
	}
}

func TestSub2APIResponseReferenceRejectsUnsignedTamperedAndMalformed(t *testing.T) {
	writeSub2APIConfig(t, "http://127.0.0.1:28090", nil)
	owner := Sub2APISubject{UserID: 68, WorkspaceTeamID: 11}
	id, err := Sub2APIWrapResponseID(owner, "resp_upstream")
	require.NoError(t, err)
	for _, bad := range []string{"resp_upstream", "", id + "0", id[:len(id)-1], strings.Replace(id, ".", ".0", 1), sub2APIResponsePrefix + "%%%..", strings.Repeat("x", 801)} {
		_, err = Sub2APIUnwrapResponseID(owner, bad)
		require.ErrorIs(t, err, ErrSub2APIResponseNotOwned)
	}
	for _, body := range []string{`{"previous_response_id":{}}`, `{"previous_response_id":false}`, `{"previous_response_id":"resp_upstream"}`, `{"input":[{"type":"item_reference","id":"msg_foreign"}]}`, `{"conversation":"conv_foreign"}`, `{"conversation":{"id":"conv_foreign"}}`} {
		_, err = Sub2APIScopeRequestBody(owner, []byte(body))
		require.ErrorIs(t, err, ErrSub2APIResponseNotOwned)
	}
}

func TestSub2APIResponseScopePreservesNativePayloadAndUnwrapsContinuation(t *testing.T) {
	writeSub2APIConfig(t, "http://127.0.0.1:28090", nil)
	owner := Sub2APISubject{UserID: 68}
	body := []byte(`{"type":"response.completed","response":{"id":"resp_saved","previous_response_id":"resp_prior","usage":{"input_tokens":23,"output_tokens":515},"output":[{"id":"msg_1","text":"resp_saved","encrypted_content":"opaque"}]},"future":9007199254740993,"zero":0,"flag":false}`)
	scoped, err := Sub2APIScopeResponseBody(owner, body)
	require.NoError(t, err)
	require.Equal(t, "9007199254740993", gjson.GetBytes(scoped, "future").Raw)
	require.Equal(t, "0", gjson.GetBytes(scoped, "zero").Raw)
	require.Equal(t, "false", gjson.GetBytes(scoped, "flag").Raw)
	require.Equal(t, gjson.GetBytes(body, "response.output").Raw, gjson.GetBytes(scoped, "response.output").Raw)
	require.Equal(t, gjson.GetBytes(body, "response.usage").Raw, gjson.GetBytes(scoped, "response.usage").Raw)
	id := gjson.GetBytes(scoped, "response.id").String()
	raw, err := common.Marshal(map[string]any{"previous_response_id": id, "max_output_tokens": 0, "store": false})
	require.NoError(t, err)
	upstream, err := Sub2APIScopeRequestBody(owner, raw)
	require.NoError(t, err)
	require.Equal(t, "resp_saved", gjson.GetBytes(upstream, "previous_response_id").String())
	require.Equal(t, "0", gjson.GetBytes(upstream, "max_output_tokens").Raw)
	require.Equal(t, "false", gjson.GetBytes(upstream, "store").Raw)
	cancel, err := Sub2APIUnscopeResponseReference(owner, []byte(`{"type":"response.cancel","response_id":"`+id+`"}`), "response_id")
	require.NoError(t, err)
	require.Equal(t, "resp_saved", gjson.GetBytes(cancel, "response_id").String())
}

func TestSub2APIResponseScopePersistsAcrossConfigReloadAndSeparatesNamespaces(t *testing.T) {
	path := writeSub2APIConfig(t, "http://127.0.0.1:28090", nil)
	owner := Sub2APISubject{UserID: 68, WorkspaceTeamID: 11}
	id, err := Sub2APIWrapResponseID(owner, "resp_shared")
	require.NoError(t, err)
	cfg, err := LoadSub2APIConfig()
	require.NoError(t, err)
	cfg.Provision.Concurrency++
	data, err := common.Marshal(cfg)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(path, data, 0600))
	_, err = Sub2APIUnwrapResponseID(owner, id)
	require.NoError(t, err)
	cfg.Namespace = "another-deployment"
	data, err = common.Marshal(cfg)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(path, data, 0600))
	_, err = Sub2APIUnwrapResponseID(owner, id)
	require.ErrorIs(t, err, ErrSub2APIResponseNotOwned)
}
