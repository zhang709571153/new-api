package model

import (
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

type WorkspaceTeamProvision struct {
	Team         *WorkspaceTeam    `json:"team"`
	Subscription *UserSubscription `json:"subscription"`
}

// Administrator provisioning grants service credit without creating a payment
// or touching either wallet. Team, membership and subscription commit together.
func AdminProvisionWorkspaceTeam(userID int, name string, weekly int64) (*WorkspaceTeamProvision, error) {
	if userID <= 0 || weekly <= 0 || weekly > common.MaxWalletQuota {
		return nil, errors.New("用户或周额度无效")
	}
	name = strings.TrimSpace(name)
	if utf8.RuneCountInString(name) > 40 {
		return nil, errors.New("团队名称须为 1–40 个字")
	}
	var result WorkspaceTeamProvision
	var revokedKeys []string
	err := DB.Transaction(func(tx *gorm.DB) error {
		user, err := lockBillingUser(tx, userID)
		if err != nil {
			return err
		}
		var owned int64
		if err := tx.Model(&WorkspaceTeam{}).Where("owner_user_id = ?", userID).Count(&owned).Error; err != nil {
			return err
		}
		if owned > 0 {
			return errors.New("该用户已拥有团队，请编辑现有团队订阅")
		}
		var member WorkspaceMember
		if err := tx.Where("user_id = ?", userID).Limit(1).Find(&member).Error; err != nil {
			return err
		}
		if member.TeamID > 0 {
			if _, revokedKeys, err = LeaveWorkspaceTeamTx(tx, userID, member.TeamID); err != nil {
				return err
			}
		}
		if name == "" {
			name = strings.TrimSpace(user.DisplayName)
			if name == "" {
				name = user.Username
			}
			if utf8.RuneCountInString(name) > 36 {
				name = string([]rune(name)[:36])
			}
			name += " 的团队"
		}
		result.Team, err = CreateWorkspaceTeamTx(tx, userID, name)
		if err != nil {
			return err
		}
		result.Subscription, err = SetWorkspaceWeeklySubscriptionTx(tx, userID, result.Team.ID, weekly)
		return err
	})
	if err != nil {
		return nil, err
	}
	for _, key := range revokedKeys {
		if err := invalidateTokenCacheForMutation(key); err != nil {
			common.SysError("provisioned team member token cache invalidation failed")
		}
	}
	if err := invalidateUserCache(userID); err != nil {
		common.SysError("provisioned team user cache invalidation failed")
	}
	return &result, nil
}
