package model

import (
	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
	"strings"
	"unicode/utf8"
)

// Administrative management is scoped to an immutable team ID. Role checks
// occur under the same transaction as membership changes.
type WorkspaceTeamManagement struct {
	ActorID int
	TeamID  int
}

func (m *WorkspaceTeamManagement) authorize(tx *gorm.DB, team *WorkspaceTeam, memberID int) error {
	if m == nil || m.TeamID <= 0 || team.ID != m.TeamID {
		return ErrWorkspaceAccess
	}
	var actor User
	if err := tx.First(&actor, m.ActorID).Error; err != nil || actor.Status != common.UserStatusEnabled || actor.Role < common.RoleAdminUser {
		return ErrWorkspaceAccess
	}
	// Match funding and membership lock order: team, account, users, tokens.
	if team.FundingVersion > 0 {
		var account WorkspaceTeamAccount
		if err := lockForUpdate(tx).First(&account, "team_id = ?", team.ID).Error; err != nil {
			return err
		}
	}
	for _, id := range []int{team.OwnerUserID, memberID} {
		if id <= 0 {
			continue
		}
		var target User
		if err := lockForUpdate(tx.Unscoped()).First(&target, id).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if actor.Role != common.RoleRootUser && actor.Role <= target.Role {
			return ErrWorkspaceAccess
		}
	}
	return nil
}

func RenameWorkspaceTeam(ownerID, teamID int, name string, management ...*WorkspaceTeamManagement) error {
	name = strings.TrimSpace(name)
	if name == "" || utf8.RuneCountInString(name) > 100 {
		return ErrWorkspaceAccess
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		var team WorkspaceTeam
		if err := lockForUpdate(tx).First(&team, "id = ? AND owner_user_id = ?", teamID, ownerID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if len(management) > 0 && management[0] != nil {
			if err := management[0].authorize(tx, &team, 0); err != nil {
				return err
			}
		}
		return tx.Model(&team).Update("name", name).Error
	})
}

func AdminRemoveWorkspaceMember(management *WorkspaceTeamManagement, memberID int) error {
	if management == nil || memberID <= 0 {
		return ErrWorkspaceAccess
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		var team WorkspaceTeam
		if err := lockForUpdate(tx).First(&team, management.TeamID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if err := management.authorize(tx, &team, memberID); err != nil {
			return err
		}
		if team.OwnerUserID == memberID {
			return ErrWorkspaceAccess
		}
		_, _, err := leaveWorkspaceTeamTx(tx, memberID, team.ID, team.OwnerUserID, true)
		return err
	})
}
