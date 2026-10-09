package model

import (
	"slices"
	"strconv"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

type WorkspaceTeamLogFilter struct {
	MemberID          int    `form:"member_id" binding:"min=0"`
	Type              int    `form:"type" binding:"oneof=0 2 5 6"`
	StartTimestamp    int64  `form:"start_timestamp" binding:"min=0"`
	EndTimestamp      int64  `form:"end_timestamp" binding:"min=0"`
	ModelName         string `form:"model_name"`
	TokenName         string `form:"token_name"`
	Group             string `form:"group"`
	RequestID         string `form:"request_id"`
	UpstreamRequestID string `form:"upstream_request_id"`
}

type WorkspaceLogMember struct {
	UserID int    `json:"user_id"`
	Name   string `json:"name"`
	Former bool   `json:"former"`
}

type workspaceTeamLogReader struct {
	team   WorkspaceTeam
	tokens []Token
}

func resolveWorkspaceTeamLogScope(ownerID int) (*workspaceTeamLogReader, error) {
	scope := &workspaceTeamLogReader{}
	// The team comes only from the authenticated owner, never query parameters.
	if err := DB.First(&scope.team, "owner_user_id = ?", ownerID).Error; err != nil {
		return nil, ErrWorkspaceAccess
	}
	if err := DB.Unscoped().Select("id, workspace_user_id").Where("user_id = ? AND workspace_user_id > 0", ownerID).Find(&scope.tokens).Error; err != nil {
		return nil, err
	}
	return scope, nil
}

func (scope *workspaceTeamLogReader) query(filter WorkspaceTeamLogFilter, withTime bool) (*gorm.DB, error) {
	ids := make([]int, 0, len(scope.tokens))
	for _, token := range scope.tokens {
		if filter.MemberID == 0 || token.WorkspaceUserID == filter.MemberID {
			ids = append(ids, token.Id)
		}
	}
	query := workspaceTeamLogScope(LOG_DB.Model(&Log{}), &scope.team).
		Where("user_id = ? AND token_id IN ? AND type IN ?", scope.team.OwnerUserID, ids, []int{LogTypeConsume, LogTypeError, LogTypeRefund})
	if filter.Type != LogTypeUnknown {
		query = query.Where("type = ?", filter.Type)
	}
	var err error
	if query, err = applyExplicitLogTextFilter(query, "model_name", filter.ModelName); err != nil {
		return nil, err
	}
	if filter.TokenName != "" {
		query = query.Where("token_name = ?", filter.TokenName)
	}
	if filter.Group != "" {
		query = query.Where(logGroupCol+" = ?", filter.Group)
	}
	if filter.RequestID != "" {
		query = query.Where("request_id = ?", filter.RequestID)
	}
	if filter.UpstreamRequestID != "" {
		query = query.Where("upstream_request_id = ?", filter.UpstreamRequestID)
	}
	if withTime && filter.StartTimestamp > 0 {
		query = query.Where("created_at >= ?", filter.StartTimestamp)
	}
	if withTime && filter.EndTimestamp > 0 {
		query = query.Where("created_at <= ?", filter.EndTimestamp)
	}
	return query, nil
}

func GetWorkspaceTeamLogs(ownerID int, filter WorkspaceTeamLogFilter, startIdx, num int) ([]*Log, []WorkspaceLogMember, int64, error) {
	scope, err := resolveWorkspaceTeamLogScope(ownerID)
	if err != nil {
		return nil, nil, 0, err
	}
	query, err := scope.query(filter, true)
	if err != nil {
		return nil, nil, 0, err
	}
	var total int64
	if err = query.Count(&total).Error; err != nil {
		return nil, nil, 0, err
	}
	logs := []*Log{}
	order := "id desc"
	if common.UsingLogDatabase(common.DatabaseTypeClickHouse) {
		order = clickHouseLogOrder("")
	}
	if err = query.Order(order).Offset(startIdx).Limit(num).Find(&logs).Error; err != nil {
		return nil, nil, 0, err
	}
	// The selector includes current members plus callers in this team's history.
	// Do not infer membership from the funding user_id stored in consumption logs.
	var current []WorkspaceMember
	if err = DB.Where("team_id = ?", scope.team.ID).Find(&current).Error; err != nil {
		return nil, nil, 0, err
	}
	currentIDs := make(map[int]bool, len(current))
	visibleIDs := make(map[int]bool, len(current))
	for _, member := range current {
		currentIDs[member.UserID], visibleIDs[member.UserID] = true, true
	}
	history, err := scope.query(WorkspaceTeamLogFilter{StartTimestamp: filter.StartTimestamp, EndTimestamp: filter.EndTimestamp}, true)
	if err != nil {
		return nil, nil, 0, err
	}
	var usedTokens []int
	if err = history.Distinct("token_id").Pluck("token_id", &usedTokens).Error; err != nil {
		return nil, nil, 0, err
	}
	holders := make(map[int]int, len(scope.tokens))
	for _, token := range scope.tokens {
		holders[token.Id] = token.WorkspaceUserID
	}
	for _, id := range usedTokens {
		visibleIDs[holders[id]] = true
	}
	ids := make([]int, 0, len(visibleIDs))
	for id := range visibleIDs {
		ids = append(ids, id)
	}
	slices.Sort(ids)
	var users []User
	if err = DB.Unscoped().Select("id, username, display_name").Where("id IN ?", ids).Find(&users).Error; err != nil {
		return nil, nil, 0, err
	}
	names := make(map[int]string, len(users))
	for _, user := range users {
		name := user.DisplayName
		if name == "" {
			name = user.Username
		}
		names[user.Id] = name
	}
	members := make([]WorkspaceLogMember, 0, len(ids))
	for _, id := range ids {
		if names[id] == "" {
			names[id] = "#" + strconv.Itoa(id)
		}
		members = append(members, WorkspaceLogMember{UserID: id, Name: names[id], Former: !currentIDs[id]})
	}
	formatUserLogs(logs, startIdx)
	for _, log := range logs {
		// Response-only caller attribution; the persisted funding identity stays intact.
		log.UserId = holders[log.TokenId]
		log.Username = names[log.UserId]
		log.ChannelId, log.TokenId, log.Ip = 0, 0, ""
	}
	return logs, members, total, nil
}

func GetWorkspaceTeamLogStats(ownerID int, filter WorkspaceTeamLogFilter) (Stat, error) {
	var stat Stat
	scope, err := resolveWorkspaceTeamLogScope(ownerID)
	if err != nil {
		return stat, err
	}
	query, err := scope.query(filter, true)
	if err != nil {
		return stat, err
	}
	if err = query.Where("type = ?", LogTypeConsume).Select("COALESCE(SUM(quota), 0) AS quota").Scan(&stat).Error; err != nil {
		return stat, err
	}
	rateQuery, err := scope.query(filter, false)
	if err != nil {
		return stat, err
	}
	var rate Stat
	err = rateQuery.Where("type = ? AND created_at >= ?", LogTypeConsume, time.Now().Add(-time.Minute).Unix()).
		Select("COUNT(*) AS rpm, COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tpm").Scan(&rate).Error
	stat.Rpm, stat.Tpm = rate.Rpm, rate.Tpm
	return stat, err
}
