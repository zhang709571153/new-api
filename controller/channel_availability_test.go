package controller

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	perfmetrics "github.com/QuantumNous/new-api/pkg/perf_metrics"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestChannelAvailability(t *testing.T) {
	setupTeamDatabase(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Channel{}, &model.ChannelAvailabilityMetric{}))
	t.Cleanup(perfmetrics.FlushChannelAttempts)
	channels := []model.Channel{
		{Name: "working", Status: common.ChannelStatusEnabled, Key: "secret-not-public"},
		{Name: "idle", Status: common.ChannelStatusEnabled},
		{Name: "manual", Status: common.ChannelStatusManuallyDisabled},
		{Name: "quota", Status: common.ChannelStatusAutoDisabled},
		{Name: "failed", Status: common.ChannelStatusAutoDisabled, OtherInfo: `{"status_reason":"sk-private raw upstream error"}`},
	}
	channels[3].SetOtherInfo(map[string]interface{}{"status_reason": service.CodexQuotaExhaustedReason})
	require.NoError(t, model.DB.Create(&channels).Error)
	now := time.Now()
	var wg sync.WaitGroup
	errs := make(chan error, 16)
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			errs <- model.RecordChannelAvailability(channels[0].Id, i%4 != 0, 100, now.Unix())
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		require.NoError(t, err)
	}
	require.NoError(t, model.RecordChannelAvailability(channels[0].Id, false, 100, now.Add(-48*time.Hour).Unix()))
	info := &relaycommon.RelayInfo{StartTime: now}
	// Cancellations and local allowance rejections do not change provider health.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	perfmetrics.RecordChannelAttempt(ctx, channels[1].Id, now, info, nil)
	perfmetrics.RecordChannelAttempt(context.Background(), channels[1].Id, now, info, types.NewErrorWithStatusCode(errors.New("quota"), types.ErrorCodeInsufficientUserQuota, 403))
	rows, err := model.GetChannelAvailability(24, now)
	require.NoError(t, err)
	require.Len(t, rows, 5)
	require.EqualValues(t, 16, rows[0].RequestCount)
	require.EqualValues(t, 12, rows[0].SuccessCount)
	require.Equal(t, 75.0, *rows[0].SuccessRate)
	require.EqualValues(t, 100, *rows[0].AvgLatencyMs)
	require.Nil(t, rows[1].SuccessRate)
	require.Equal(t, "no_data", rows[1].SampleState)
	require.Equal(t, "manually_disabled", rows[2].RoutingState)
	require.Equal(t, "quota_exhausted", rows[3].RoutingState)
	require.Equal(t, "automatically_disabled", rows[4].RoutingState)
	require.Len(t, rows[0].Series, 24)
	require.Nil(t, rows[0].Series[0].SuccessRate)
	week, err := model.GetChannelAvailability(168, now)
	require.NoError(t, err)
	require.EqualValues(t, 17, week[0].RequestCount)
	require.NoError(t, model.DeleteChannelAvailabilityBefore(now.Add(-24*time.Hour).Unix()))
	week, err = model.GetChannelAvailability(168, now)
	require.NoError(t, err)
	require.EqualValues(t, 16, week[0].RequestCount)

	root := model.User{Username: "availability-root", AffCode: "availability-root", Role: common.RoleRootUser, Status: 1, AuthVersion: 1}
	user := model.User{Username: "availability-user", AffCode: "availability-user", Role: common.RoleCommonUser, Status: 1, AuthVersion: 1}
	require.NoError(t, model.DB.Create(&root).Error)
	require.NoError(t, model.DB.Create(&user).Error)
	rs, err := service.CreateLoginSession(root.Id, "password", "127.0.0.1", "availability-test")
	require.NoError(t, err)
	us, err := service.CreateLoginSession(user.Id, "password", "127.0.0.1", "availability-test")
	require.NoError(t, err)
	r := gin.New()
	r.GET("/health", middleware.AdminAuth(), GetChannelAvailability)
	call := func(path, credential string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("GET", path, nil)
		req.Header.Set("Authorization", "Bearer "+credential)
		w := httptest.NewRecorder()
		r.ServeHTTP(w, req)
		return w
	}
	require.NotEqual(t, 200, call("/health", "").Code)
	require.Equal(t, 403, call("/health", us.AccessToken).Code)
	w := call("/health?hours=24", rs.AccessToken)
	require.Equal(t, 200, w.Code)
	require.Equal(t, "no-store", w.Header().Get("Cache-Control"))
	require.NotContains(t, w.Body.String(), "sk-private")
	require.NotContains(t, w.Body.String(), "secret-not-public")
	require.NotContains(t, w.Body.String(), "other_info")
	for _, query := range []string{"0", "169", "bogus", "-1"} {
		require.Equal(t, 400, call("/health?hours="+query, rs.AccessToken).Code)
	}
	t.Run("handler panic is a failed channel attempt", func(t *testing.T) {
		c, _ := gin.CreateTestContext(httptest.NewRecorder())
		c.Request = httptest.NewRequest("POST", "/v1/responses", nil)
		require.Panics(t, func() {
			runObservedChannelAttempt(c, channels[1].Id, info, true, func() *types.NewAPIError { panic("fixture panic") })
		})
		perfmetrics.FlushChannelAttempts()
		var metric model.ChannelAvailabilityMetric
		require.NoError(t, model.DB.Where("channel_id = ?", channels[1].Id).First(&metric).Error)
		require.EqualValues(t, 1, metric.RequestCount)
		require.Zero(t, metric.SuccessCount)
	})
}

// Simulate a browser closing after reading the complete buffered response,
// before the handler finishes settlement and records channel health.
type cancelAfterResponseWriter struct {
	gin.ResponseWriter
	cancel context.CancelFunc
}

func (w cancelAfterResponseWriter) Write(data []byte) (int, error) {
	n, err := w.ResponseWriter.Write(data)
	w.cancel()
	return n, err
}

func TestChannelAvailabilityCodexPlayground(t *testing.T) {
	for _, tc := range []struct {
		name        string
		contentType string
		body        string
		status      int
		stream      bool
	}{
		{"upstream server error without code", "application/json", `{"error":{"message":"isolated failure","type":"server_error"}}`, 500, true},
		{"buffered SSE late client close", "text/event-stream", "", 200, false},
		{"buffered mislabeled SSE late client close", "application/json", "", 200, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fixture := newResponsesWSBillingTest(t, `tier("request", fixed(0.002))`, func(*websocket.Conn, *http.Request) {})
			fixture.httpUpstream = func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, "/backend-api/codex/responses", r.URL.Path)
				_, err := io.Copy(io.Discard, r.Body)
				assert.NoError(t, err)
				w.Header().Set("Content-Type", tc.contentType)
				w.WriteHeader(tc.status)
				body := tc.body
				if body == "" {
					body = "data: " + `{"type":"response.completed","response":{"id":"resp_health","model":"ws-billing","status":"completed","output":[{"id":"msg_health","type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}],"usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}` + "\n\n"
				}
				_, err = io.WriteString(w, body)
				assert.NoError(t, err)
			}
			require.NoError(t, model.DB.Model(fixture.channel).Updates(map[string]any{
				"type": constant.ChannelTypeCodex, "key": `{"access_token":"test-only","account_id":"test-only"}`,
				"channel_info": `{"is_multi_key":false}`,
			}).Error)
			previousRetries := common.RetryTimes
			common.RetryTimes = 0
			t.Cleanup(func() { common.RetryTimes = previousRetries })
			done := make(chan struct{})
			r := gin.New()
			r.POST("/pg/chat/completions", middleware.TokenAuth(), middleware.Distribute(), func(c *gin.Context) {
				defer close(done)
				if !tc.stream {
					ctx, cancel := context.WithCancel(c.Request.Context())
					defer cancel()
					c.Request = c.Request.WithContext(ctx)
					c.Writer = cancelAfterResponseWriter{ResponseWriter: c.Writer, cancel: cancel}
				}
				c.Set("playground_real_key", true)
				Relay(c, types.RelayFormatOpenAI)
			})
			gateway := httptest.NewServer(r)
			t.Cleanup(gateway.Close)
			body, err := common.Marshal(map[string]any{"model": "ws-billing", "stream": tc.stream, "messages": []map[string]string{{"role": "user", "content": "hello"}}})
			require.NoError(t, err)
			request, err := http.NewRequest(http.MethodPost, gateway.URL+"/pg/chat/completions", strings.NewReader(string(body)))
			require.NoError(t, err)
			request.Header.Set("Authorization", "Bearer sk-"+fixture.token.Key)
			request.Header.Set("Content-Type", "application/json")
			response, err := (&http.Client{Timeout: 3 * time.Second}).Do(request)
			require.NoError(t, err)
			_, err = io.Copy(io.Discard, response.Body)
			require.NoError(t, err)
			require.NoError(t, response.Body.Close())
			<-done
			require.Equal(t, tc.status, response.StatusCode)
			perfmetrics.FlushChannelAttempts()
			var metric model.ChannelAvailabilityMetric
			require.NoError(t, model.DB.Where("channel_id = ?", fixture.channel.Id).First(&metric).Error)
			require.EqualValues(t, 1, metric.RequestCount)
			if tc.status == http.StatusOK {
				require.EqualValues(t, 1, metric.SuccessCount)
			} else {
				require.Zero(t, metric.SuccessCount)
			}
		})
	}
}
