package service

import (
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/shopspring/decimal"
	"gorm.io/gorm"
)

type WorkspaceKey struct {
	ID        int    `json:"id"`
	MaskedKey string `json:"masked_key"`
	Status    int    `json:"status"`
}

type WorkspaceSummary struct {
	Scope                string                      `json:"scope"`
	Scopes               []string                    `json:"scopes"`
	Mode                 string                      `json:"mode"`
	BalanceUSD           float64                     `json:"balance_usd"`
	UsedUSD              float64                     `json:"used_usd"`
	APIKey               *WorkspaceKey               `json:"api_key"`
	Team                 *model.WorkspaceTeam        `json:"team"`
	PaygoBalanceUSD      float64                     `json:"paygo_balance_usd"`
	PersonalBalanceUSD   float64                     `json:"personal_balance_usd"`
	Subscriptions        []model.SubscriptionSummary `json:"subscriptions"`
	SubscriptionConflict bool                        `json:"subscription_conflict"`
	model.WorkspaceUsage
}

type WorkspaceMemberView struct {
	OpeningUsedUSD              float64 `json:"opening_used_usd"`
	EffectiveWeeklyAllowanceUSD float64 `json:"effective_weekly_allowance_usd"`
	WeeklyUsedUSD               float64 `json:"weekly_used_usd"`
	WeeklyResetAt               int64   `json:"weekly_reset_at"`
	UserID                      int     `json:"user_id"`
	Username                    string  `json:"username"`
	DisplayName                 string  `json:"display_name"`
	Status                      int     `json:"status"`
	IsOwner                     bool    `json:"is_owner"`
	BalanceUSD                  float64 `json:"balance_usd"`
	AllowanceUSD                float64 `json:"allowance_usd"`
	UsedUSD                     float64 `json:"used_usd"`
	MaskedKey                   string  `json:"masked_key"`
	model.WorkspaceUsage
}

type WorkspaceTeamView struct {
	model.WorkspaceUsage
	HasUnexpiredSubscription bool                        `json:"has_unexpired_subscription"`
	UsedUSD                  float64                     `json:"used_usd"`
	Team                     *model.WorkspaceTeam        `json:"team"`
	PoolBalanceUSD           *float64                    `json:"pool_balance_usd,omitempty"`
	Members                  []WorkspaceMemberView       `json:"members"`
	Subscriptions            []model.SubscriptionSummary `json:"subscriptions"`
	SubscriptionConflict     bool                        `json:"subscription_conflict"`
}

func GetWorkspaceSummary(userID int) (*WorkspaceSummary, error) {
	return GetWorkspaceSummaryForScope(userID, "current")
}

func GetWorkspaceSummaryForScope(userID int, scope string) (*WorkspaceSummary, error) {
	user, err := model.GetUserById(userID, false)
	if err != nil {
		return nil, err
	}
	member, team, err := model.FindWorkspaceMembership(userID)
	if err != nil {
		return nil, err
	}
	token, err := model.GetWorkspaceKeyForScope(userID, scope, false)
	if err != nil {
		return nil, err
	}
	result := &WorkspaceSummary{Mode: "personal", Scope: "personal", Scopes: []string{"personal"}, Team: team}
	result.PersonalBalanceUSD = float64(max(0, user.Quota)) / common.QuotaPerUnit
	result.PaygoBalanceUSD = result.PersonalBalanceUSD
	result.BalanceUSD, result.Subscriptions, err = model.WorkspaceFunding(userID)
	if err != nil {
		return nil, err
	}
	result.SubscriptionConflict = len(result.Subscriptions) > 1
	if team != nil {
		result.Scopes = []string{"personal", "team"}
	}
	if token != nil && token.WorkspaceUserID > 0 {
		result.Scope, result.Mode = "team", "owner"
		result.BalanceUSD, result.PaygoBalanceUSD, result.Subscriptions, err = model.WorkspaceTeamFunding(team)
		if err != nil {
			return nil, err
		}
		result.SubscriptionConflict = team.FundingVersion > 0 && len(result.Subscriptions) > 1
		if team.OwnerUserID != userID {
			result.Mode = "member"
			remaining := int64(token.RemainQuota)
			if team.FundingVersion > 0 {
				usage, _, usageErr := model.GetWorkspaceMemberWeeklyUsage(team.ID, result.Subscriptions)
				if usageErr != nil {
					return nil, usageErr
				}
				remaining = model.WorkspaceMemberWeeklyQuota(member, token) - usage[userID]
			}
			result.BalanceUSD = max(0, min(result.BalanceUSD, float64(remaining)/common.QuotaPerUnit))
		}
		owner, err := model.GetWorkspaceAccountSnapshot(team.OwnerUserID)
		if err != nil {
			return nil, err
		}
		if member.Status != common.UserStatusEnabled || token.Status == common.TokenStatusDisabled || owner.Status != common.UserStatusEnabled {
			result.BalanceUSD = 0
		}
	}
	tokenID := 0
	if token != nil {
		result.APIKey = &WorkspaceKey{ID: token.Id, MaskedKey: "sk-" + token.GetMaskedKey(), Status: token.Status}
		if result.Scope == "team" {
			tokenID = token.Id
		}
	}
	result.WorkspaceUsage, err = model.GetWorkspaceScopedUsage(tokenID, userID, result.Scope)
	result.UsedUSD = float64(result.WorkspaceUsage.UsedQuota) / common.QuotaPerUnit
	return result, err
}

func CreateWorkspaceTeam(userID int, name string) (*model.WorkspaceTeam, error) {
	name = strings.TrimSpace(name)
	if name == "" || utf8.RuneCountInString(name) > 40 {
		return nil, errors.New("团队名称须为 1–40 个字")
	}
	return model.CreateWorkspaceTeam(userID, name)
}

// Membership permits viewing team usage. Key access and writes remain owner-only.
func GetWorkspaceTeamView(userID int, adminTeamID int) (*WorkspaceTeamView, error) {
	var team *model.WorkspaceTeam
	if adminTeamID > 0 {
		team = &model.WorkspaceTeam{}
		if err := model.DB.First(team, adminTeamID).Error; err != nil {
			return nil, err
		}
	} else {
		_, found, err := model.FindWorkspaceMembership(userID)
		if err != nil || found == nil {
			return nil, err
		}
		team = found
	}
	owner, err := model.GetWorkspaceAccountSnapshot(team.OwnerUserID)
	if err != nil {
		return nil, err
	}
	result := &WorkspaceTeamView{Team: team, Members: []WorkspaceMemberView{}}
	result.HasUnexpiredSubscription, err = model.HasUnexpiredWorkspaceTeamSubscription(team)
	if err != nil {
		return nil, err
	}
	query := model.DB.Where("team_id = ?", team.ID)
	balance, _, subscriptions, err := model.WorkspaceTeamFunding(team)
	if err != nil {
		return nil, err
	}
	result.Subscriptions = subscriptions
	weeklyUsage, weeklyResetAt, err := model.GetWorkspaceMemberWeeklyUsageSnapshot(team.ID, subscriptions)
	if err != nil {
		return nil, err
	}
	result.SubscriptionConflict = team.FundingVersion > 0 && len(subscriptions) > 1
	result.PoolBalanceUSD = &balance
	total, usageByMember, err := model.GetWorkspaceTeamUsage(team)
	if err != nil {
		return nil, err
	}
	result.UsedUSD = float64(total.UsedQuota) / common.QuotaPerUnit
	result.WorkspaceUsage = total
	var members []model.WorkspaceMember
	if err := query.Order("created_at, user_id").Find(&members).Error; err != nil {
		return nil, err
	}
	for _, member := range members {
		user, err := model.GetWorkspaceAccountSnapshot(member.UserID)
		if err != nil {
			return nil, err
		}
		var token model.Token
		err = model.DB.Unscoped().Where("id = ? AND user_id = ? AND workspace_user_id = ?", member.TokenID, owner.Id, member.UserID).First(&token).Error
		if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, err
		}
		if errors.Is(err, gorm.ErrRecordNotFound) || token.DeletedAt.Valid {
			token = model.Token{Id: member.TokenID, Status: common.TokenStatusDisabled}
		}
		row := WorkspaceMemberView{UserID: user.Id, Username: user.Username, DisplayName: user.DisplayName, Status: member.Status, IsOwner: user.Id == owner.Id}
		usage := weeklyUsage[member.UserID]
		row.OpeningUsedUSD = float64(usage.OpeningUsedQuota) / common.QuotaPerUnit
		row.WeeklyUsedUSD = (float64(usage.UsedQuota) + float64(usage.OpeningUsedQuota)) / common.QuotaPerUnit
		row.WeeklyResetAt = weeklyResetAt
		if token.Key != "" && (adminTeamID > 0 || userID == owner.Id || userID == member.UserID) {
			row.MaskedKey = "sk-" + token.GetMaskedKey()
		}
		cap := int64(token.RemainQuota)
		remaining := cap
		if team.FundingVersion > 0 {
			cap = model.WorkspaceMemberWeeklyQuota(&member, &token)
			remaining = cap - usage.UsedQuota
		}
		row.AllowanceUSD = float64(max(0, cap)) / common.QuotaPerUnit
		row.EffectiveWeeklyAllowanceUSD = (float64(max(0, cap)) + float64(usage.OpeningUsedQuota)) / common.QuotaPerUnit
		row.BalanceUSD = max(0, min(float64(remaining)/common.QuotaPerUnit, balance))
		if row.IsOwner {
			row.AllowanceUSD, row.BalanceUSD = balance, balance
			// Owners use the shared pool, never an artificial member allowance.
			row.EffectiveWeeklyAllowanceUSD = balance
		}
		if member.Status != common.UserStatusEnabled || user.Status != common.UserStatusEnabled || owner.Status != common.UserStatusEnabled || token.Status == common.TokenStatusDisabled {
			row.Status = common.UserStatusDisabled
			row.BalanceUSD = 0
		}
		row.WorkspaceUsage = usageByMember[member.UserID]
		row.UsedUSD = float64(row.UsedQuota) / common.QuotaPerUnit
		result.Members = append(result.Members, row)
	}
	return result, nil
}

type WorkspaceMemberUpdate struct {
	AllowanceUSD *decimal.Decimal `json:"allowance_usd"`
	Status       *int             `json:"status"`
	DisplayName  *string          `json:"display_name"`
}

func UpdateWorkspaceMember(ownerID, memberID int, request WorkspaceMemberUpdate, management ...*model.WorkspaceTeamManagement) error {
	if request.AllowanceUSD == nil && request.Status == nil && request.DisplayName == nil {
		return errors.New("请填写成员昵称、额度或状态")
	}
	if request.Status != nil && *request.Status != common.UserStatusEnabled && *request.Status != common.UserStatusDisabled {
		return errors.New("成员状态无效")
	}
	var quota *int
	if request.AllowanceUSD != nil {
		if request.AllowanceUSD.IsNegative() || request.AllowanceUSD.GreaterThan(decimal.NewFromInt(1_000_000_000)) {
			return errors.New("额度须为 0–1000000000 美元")
		}
		value, err := common.WalletQuotaFromDecimalStrict(request.AllowanceUSD.Mul(decimal.NewFromFloat(common.QuotaPerUnit)))
		if err != nil {
			return errors.New("额度超出允许范围")
		}
		quota = &value
	}
	return model.UpdateWorkspaceMember(ownerID, memberID, quota, request.Status, request.DisplayName, management...)
}
