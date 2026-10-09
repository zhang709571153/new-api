package openai

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
)

func TestSub2APIHTTPAndSSEExposeOnlyOwnedResponseReferences(t *testing.T) {
	oldTimeout := constant.StreamingTimeout
	constant.StreamingTimeout = 30
	t.Cleanup(func() { constant.StreamingTimeout = oldTimeout })
	cfg := service.Sub2APIConfig{Version: 1, Namespace: "wire-scope-test", IdentitySecret: strings.Repeat("s", 32), AdminAPIKey: "fixture-only", Pools: []service.Sub2APIPool{{ChannelID: 59, BaseURL: "http://127.0.0.1:28090", GroupID: 10}}}
	cfg.Provision.Enabled, cfg.Provision.Mode, cfg.Provision.InitialBalance, cfg.Provision.Concurrency = true, "isolated-lazy", 100, 10
	raw, err := common.Marshal(cfg)
	require.NoError(t, err)
	path := filepath.Join(t.TempDir(), "bindings.json")
	require.NoError(t, os.WriteFile(path, raw, 0600))
	t.Setenv("REALYU_UPSTREAM_DRIVER", "sub2api")
	t.Setenv("REALYU_SUB2API_BINDINGS_FILE", path)
	for _, stream := range []bool{false, true} {
		t.Run(map[bool]string{false: "json", true: "sse"}[stream], func(t *testing.T) {
			w := httptest.NewRecorder()
			c, _ := gin.CreateTestContext(w)
			c.Request = httptest.NewRequest(http.MethodPost, "/v1/responses", nil)
			c.Set("id", 68)
			info := &relaycommon.RelayInfo{OriginModelName: "gpt-6.1-sol", DisablePing: true, ChannelMeta: &relaycommon.ChannelMeta{ChannelType: constant.ChannelTypeSub2API}}
			body := `{"id":"resp_wire","object":"response","status":"completed","output":[],"usage":{"input_tokens":23,"output_tokens":515,"total_tokens":538},"future":9007199254740993,"zero":0,"flag":false}`
			if stream {
				body = "data: {\"type\":\"response.completed\",\"response\":" + body + "}\n\n"
			}
			resp := &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}
			if stream {
				usage, apiErr := OaiResponsesStreamHandler(c, info, resp)
				require.Nil(t, apiErr)
				require.Equal(t, 538, usage.TotalTokens)
			} else {
				usage, apiErr := OaiResponsesHandler(c, info, resp)
				require.Nil(t, apiErr)
				require.Equal(t, 538, usage.TotalTokens)
			}
			wire := w.Body.String()
			jsonBody, idPath := wire, "id"
			if stream {
				for _, line := range strings.Split(wire, "\n") {
					if strings.HasPrefix(line, "data: ") {
						jsonBody = strings.TrimPrefix(line, "data: ")
						break
					}
				}
				idPath = "response.id"
			}
			id := gjson.Get(jsonBody, idPath).String()
			upstream, err := service.Sub2APIUnwrapResponseID(service.Sub2APISubject{UserID: 68}, id)
			require.NoError(t, err)
			require.Equal(t, "resp_wire", upstream)
			_, err = service.Sub2APIUnwrapResponseID(service.Sub2APISubject{UserID: 67}, id)
			require.ErrorIs(t, err, service.ErrSub2APIResponseNotOwned)
			require.Contains(t, wire, "9007199254740993")
			require.Contains(t, wire, `"flag":false`)
		})
	}
}
