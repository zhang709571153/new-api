package service

import (
	"slices"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
)

type PlaygroundContext struct {
	Scope           string        `json:"scope"`
	Scopes          []string      `json:"scopes"`
	Group           string        `json:"group"`
	Models          []string      `json:"models"`
	Key             *WorkspaceKey `json:"key"`
	BalanceUSD      float64       `json:"balance_usd"`
	PaygoBalanceUSD float64       `json:"paygo_balance_usd"`
	CanChat         bool          `json:"can_chat"`
}

func GetPlaygroundContext(userID int, scope string) (*PlaygroundContext, error) {
	token, err := model.GetPlaygroundKey(userID, scope)
	if err != nil || token == nil {
		return nil, model.ErrWorkspaceAccess
	}
	member, team, err := model.FindWorkspaceMembership(userID)
	if err != nil {
		return nil, err
	}
	result := &PlaygroundContext{Scope: "personal", Scopes: []string{"personal"}, Models: []string{}, Key: &WorkspaceKey{ID: token.Id, MaskedKey: "sk-" + token.GetMaskedKey(), Status: token.Status}}
	if team != nil {
		result.Scopes = []string{"personal", "team"}
		if token.WorkspaceUserID > 0 {
			result.Scope = "team"
		}
	}
	owner, err := model.GetUserById(token.UserId, false)
	if err != nil {
		return nil, err
	}
	result.BalanceUSD, _, err = model.WorkspaceFunding(token.UserId)
	if err != nil {
		return nil, err
	}
	result.PaygoBalanceUSD = float64(max(0, owner.Quota)) / common.QuotaPerUnit
	if result.Scope == "team" {
		result.BalanceUSD, result.PaygoBalanceUSD, _, err = model.WorkspaceTeamFunding(team)
		if err != nil {
			return nil, err
		}
	}
	if !token.UnlimitedQuota {
		result.BalanceUSD = max(0, min(result.BalanceUSD, float64(token.RemainQuota)/common.QuotaPerUnit))
	}
	result.CanChat = owner.Status == common.UserStatusEnabled && token.Status == common.TokenStatusEnabled && (token.ExpiredTime == -1 || token.ExpiredTime > common.GetTimestamp()) && result.BalanceUSD > 0
	if result.Scope == "team" && member.Status != common.UserStatusEnabled {
		result.CanChat = false
	}
	result.Group = owner.Group
	if token.Group != "" {
		result.Group = token.Group
	}
	groups := []string{result.Group}
	if result.Group == "auto" {
		groups = GetUserAutoGroup(owner.Group)
	}
	limits := token.GetModelLimitsMap()
	for _, name := range GetGroupsEnabledModels(groups) {
		if strings.HasPrefix(name, "gpt-image-") || (token.ModelLimitsEnabled && !limits[name]) {
			continue
		}
		result.Models = append(result.Models, name)
	}
	slices.Sort(result.Models)
	return result, nil
}
