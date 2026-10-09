package model

import (
	"context"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// Hourly aggregate only: no credentials, request bodies, users or raw errors.
// A separate table preserves the existing model-level, final-request metric.
type ChannelAvailabilityMetric struct {
	ChannelID      int   `gorm:"primaryKey;autoIncrement:false"`
	BucketTs       int64 `gorm:"primaryKey;autoIncrement:false;index"`
	RequestCount   int64
	SuccessCount   int64
	TotalLatencyMs int64
}

func RecordChannelAvailability(channelID int, success bool, latencyMs, timestamp int64) error {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if channelID <= 0 {
		return nil
	}
	row := ChannelAvailabilityMetric{ChannelID: channelID, BucketTs: timestamp - timestamp%3600, RequestCount: 1, TotalLatencyMs: max(0, latencyMs)}
	if success {
		row.SuccessCount = 1
	}
	return DB.WithContext(ctx).Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "channel_id"}, {Name: "bucket_ts"}}, DoUpdates: clause.Assignments(map[string]any{
		"request_count":    gorm.Expr("channel_availability_metrics.request_count + ?", 1),
		"success_count":    gorm.Expr("channel_availability_metrics.success_count + ?", row.SuccessCount),
		"total_latency_ms": gorm.Expr("channel_availability_metrics.total_latency_ms + ?", row.TotalLatencyMs),
	})}).Create(&row).Error
}

type ChannelAvailabilityPoint struct {
	Ts           int64    `json:"ts"`
	RequestCount int64    `json:"request_count"`
	SuccessRate  *float64 `json:"success_rate"`
}
type ChannelAvailability struct {
	ID           int                        `json:"id"`
	Name         string                     `json:"name"`
	RoutingState string                     `json:"routing_state"`
	SampleState  string                     `json:"sample_state"`
	RequestCount int64                      `json:"request_count"`
	SuccessCount int64                      `json:"success_count"`
	SuccessRate  *float64                   `json:"success_rate"`
	AvgLatencyMs *int64                     `json:"avg_latency_ms"`
	Series       []ChannelAvailabilityPoint `json:"series"`
}

func GetChannelAvailability(hours int, now time.Time) ([]ChannelAvailability, error) {
	hours = max(1, min(hours, 168))
	end := now.Unix()
	end -= end % 3600
	start := end - int64(hours-1)*3600
	// Explicit allowlist prevents future Channel fields from leaking into this API.
	var channels []Channel
	if err := DB.Select("id", "name", "status", "other_info").Order("id ASC").Find(&channels).Error; err != nil {
		return nil, err
	}
	var metrics []ChannelAvailabilityMetric
	if err := DB.Where("bucket_ts >= ? AND bucket_ts <= ?", start, end).Find(&metrics).Error; err != nil {
		return nil, err
	}
	buckets := map[int]map[int64]ChannelAvailabilityMetric{}
	for _, m := range metrics {
		if buckets[m.ChannelID] == nil {
			buckets[m.ChannelID] = map[int64]ChannelAvailabilityMetric{}
		}
		buckets[m.ChannelID][m.BucketTs] = m
	}
	result := make([]ChannelAvailability, 0, len(channels))
	for _, ch := range channels {
		row := ChannelAvailability{ID: ch.Id, Name: ch.Name, RoutingState: "enabled", SampleState: "no_data", Series: make([]ChannelAvailabilityPoint, 0, hours)}
		switch ch.Status {
		case common.ChannelStatusManuallyDisabled:
			row.RoutingState = "manually_disabled"
		case common.ChannelStatusAutoDisabled:
			row.RoutingState = "automatically_disabled"
			if ch.GetOtherInfo()["status_reason"] == "codex_usage_limit_reached" {
				row.RoutingState = "quota_exhausted"
			}
		case common.ChannelStatusEnabled:
		default:
			row.RoutingState = "unavailable"
		}
		var latency int64
		for ts := start; ts <= end; ts += 3600 {
			metric := buckets[ch.Id][ts]
			p := ChannelAvailabilityPoint{Ts: ts, RequestCount: metric.RequestCount}
			if metric.RequestCount > 0 {
				rate := float64(metric.SuccessCount) * 100 / float64(metric.RequestCount)
				p.SuccessRate = &rate
			}
			row.Series = append(row.Series, p)
			row.RequestCount += metric.RequestCount
			row.SuccessCount += metric.SuccessCount
			latency += metric.TotalLatencyMs
		}
		if row.RequestCount > 0 {
			rate := float64(row.SuccessCount) * 100 / float64(row.RequestCount)
			avg := latency / row.RequestCount
			row.SuccessRate = &rate
			row.AvgLatencyMs = &avg
			row.SampleState = "healthy"
			if rate < 99 {
				row.SampleState = "degraded"
			}
			if row.SuccessCount == 0 {
				row.SampleState = "failing"
			}
		}
		result = append(result, row)
	}
	return result, nil
}

func DeleteChannelAvailabilityBefore(cutoff int64) error {
	return DB.Where("bucket_ts < ?", cutoff).Delete(&ChannelAvailabilityMetric{}).Error
}
