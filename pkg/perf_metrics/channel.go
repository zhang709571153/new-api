package perfmetrics

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/setting/perf_metrics_setting"
)

type channelSample struct {
	id                 int
	success            bool
	latency, timestamp int64
}

var channelSamples = make(chan channelSample, 4096)
var channelSamplesDropped atomic.Int64

func ChannelSamplesDropped() int64 { return channelSamplesDropped.Load() }

func persistChannelSample(sample channelSample) {
	if err := model.RecordChannelAvailability(sample.id, sample.success, sample.latency, sample.timestamp); err != nil {
		channelSamplesDropped.Add(1)
	}
}

func channelSampleLoop() {
	for sample := range channelSamples {
		persistChannelSample(sample)
	}
}

// FlushChannelAttempts is for controlled shutdowns and isolated tests without
// the background worker. Requests never wait for diagnostic database writes.
func FlushChannelAttempts() {
	for {
		select {
		case sample := <-channelSamples:
			persistChannelSample(sample)
		default:
			return
		}
	}
}

// Each attempted channel is sampled separately, before retry changes its ID.
// User-facing model statistics continue to count the final request only.
func RecordChannelAttempt(ctx context.Context, channelID int, started time.Time, info *relaycommon.RelayInfo, apiErr *types.NewAPIError) {
	if !perf_metrics_setting.GetSetting().Enabled {
		return
	}
	outcome := ClassifyRelayOutcome(ctx, info, apiErr)
	if outcome == OutcomeIgnored || channelID <= 0 {
		return
	}
	select {
	case channelSamples <- channelSample{channelID, outcome == OutcomeSuccess, time.Since(started).Milliseconds(), time.Now().Unix()}:
	default:
		channelSamplesDropped.Add(1)
	}
}
