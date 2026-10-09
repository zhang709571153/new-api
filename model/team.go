package model

import (
	"sort"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

// TeamMember deliberately excludes passwords, login tokens and upstream credentials.
type TeamMember struct {
	Id               int    `json:"id"`
	Username         string `json:"username"`
	DisplayName      string `json:"display_name"`
	Status           int    `json:"status"`
	Quota            int    `json:"quota"`
	UsedQuota        int    `json:"used_quota"`
	RequestCount     int    `json:"request_count"`
	PeriodQuota      int64  `json:"period_quota"`
	PeriodRequests   int64  `json:"period_requests"`
	PromptTokens     int64  `json:"prompt_tokens"`
	CompletionTokens int64  `json:"completion_tokens"`
	LastRequestAt    int64  `json:"last_request_at"`
}

type TeamTotals struct {
	Quota            int64 `json:"quota"`
	Requests         int64 `json:"requests"`
	PromptTokens     int64 `json:"prompt_tokens"`
	CompletionTokens int64 `json:"completion_tokens"`
}

type TeamOverview struct {
	Members        []TeamMember `json:"members"`
	Totals         TeamTotals   `json:"totals"`
	StartTimestamp int64        `json:"start_timestamp"`
	EndTimestamp   int64        `json:"end_timestamp"`
	QuotaPerUnit   float64      `json:"quota_per_unit"`
}

func GetTeamOverview(start, end int64) (*TeamOverview, error) {
	result := &TeamOverview{Members: []TeamMember{}, StartTimestamp: start, EndTimestamp: end, QuotaPerUnit: common.QuotaPerUnit}
	if err := DB.Model(&User{}).Select("id, username, display_name, status, quota, used_quota, request_count").
		Where("role = ?", common.RoleCommonUser).Scan(&result.Members).Error; err != nil {
		return nil, err
	}
	// LOG_DB may be a separate database. Never join it to the users table.
	var usage []struct {
		UserId int
		TeamTotals
		LastRequestAt int64
	}
	if err := LOG_DB.Model(&Log{}).
		Select("user_id, SUM(quota) AS quota, COUNT(*) AS requests, SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens, MAX(created_at) AS last_request_at").
		Where("type = ? AND created_at >= ? AND created_at < ?", LogTypeConsume, start, end).
		Group("user_id").Scan(&usage).Error; err != nil {
		return nil, err
	}
	byUser := make(map[int]int, len(result.Members))
	for i := range result.Members {
		byUser[result.Members[i].Id] = i
	}
	for _, row := range usage {
		index, exists := byUser[row.UserId]
		if !exists {
			continue
		}
		member := &result.Members[index]
		member.PeriodQuota, member.PeriodRequests = row.Quota, row.Requests
		member.PromptTokens, member.CompletionTokens = row.PromptTokens, row.CompletionTokens
		member.LastRequestAt = row.LastRequestAt
		result.Totals.Quota += row.Quota
		result.Totals.Requests += row.Requests
		result.Totals.PromptTokens += row.PromptTokens
		result.Totals.CompletionTokens += row.CompletionTokens
	}
	sort.Slice(result.Members, func(i, j int) bool {
		if result.Members[i].PeriodQuota == result.Members[j].PeriodQuota {
			return result.Members[i].Id < result.Members[j].Id
		}
		return result.Members[i].PeriodQuota > result.Members[j].PeriodQuota
	})
	return result, nil
}

// CreateTeamMember commits the API-only member's wallet and personal key
// together, preserving the explicitly allocated quota instead of signup gifts.
func CreateTeamMember(user *User, token *Token) error {
	return DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(user).Error; err != nil {
			return err
		}
		token.UserId = user.Id
		return tx.Create(token).Error
	})
}
