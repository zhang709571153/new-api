package model

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// New teams have an independent funding account. Version zero preserves legacy
// owner-wallet behavior until an explicit asset migration. Member allowances
// remain spending caps, not transfers into a member's personal wallet.
type WorkspaceTeam struct {
	FundingVersion int    `json:"funding_version" gorm:"not null;default:0"`
	ID             int    `json:"id"`
	OwnerUserID    int    `json:"owner_user_id" gorm:"uniqueIndex"`
	Name           string `json:"name" gorm:"size:80"`
	CreatedAt      int64  `json:"created_at"`
}

type WorkspaceMember struct {
	WeeklyQuota *int64 `gorm:"type:bigint"`
	UserID      int    `gorm:"primaryKey;autoIncrement:false"`
	TeamID      int    `gorm:"index"`
	TokenID     int    `gorm:"uniqueIndex"`
	Status      int
	CreatedAt   int64
}

type WorkspaceInvite struct {
	Hash      string `gorm:"primaryKey;size:64"`
	Code      string `json:"-" gorm:"size:64"`
	TeamID    int    `gorm:"index"`
	ExpiresAt int64
	UsedBy    int
}

type WorkspacePersonalKey struct {
	UserID  int `gorm:"primaryKey;autoIncrement:false"`
	TokenID int `gorm:"uniqueIndex"`
}

var (
	ErrWorkspaceAccess    = errors.New("无权管理此团队或成员")
	ErrWorkspaceExists    = errors.New("你已加入一个团队")
	ErrWorkspaceInvite    = errors.New("邀请无效、已使用或已过期，请联系团队管理员重新邀请")
	ErrWorkspaceOwnerJoin = errors.New("你已拥有团队，不能加入其他团队")
)

// PreserveWorkspaceTokenIDs reserves physically deleted SQLite rowids referenced
// by workspace history. Legacy SQLite tables lack AUTOINCREMENT; otherwise a new
// credential can inherit a deleted key's membership and historical usage. MySQL
// and PostgreSQL already retain their generated-ID high-water mark after DELETE.
// Tombstones have random unusable keys, are disabled and soft-deleted. Startup
// runs this before serving traffic; ordinary key deletion remains a soft delete.
func PreserveWorkspaceTokenIDs() error {
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		return nil
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		var personal []WorkspacePersonalKey
		if err := tx.Find(&personal).Error; err != nil {
			return err
		}
		var members []WorkspaceMember
		if err := tx.Find(&members).Error; err != nil {
			return err
		}
		reserved := make(map[int]Token)
		for _, key := range personal {
			reserved[key.TokenID] = Token{Id: key.TokenID, UserId: key.UserID}
		}
		for _, member := range members {
			var team WorkspaceTeam
			if err := tx.First(&team, member.TeamID).Error; err != nil {
				return err
			}
			reserved[member.TokenID] = Token{Id: member.TokenID, UserId: team.OwnerUserID, WorkspaceUserID: member.UserID}
		}
		for id, token := range reserved {
			if id <= 0 {
				continue
			}
			var count int64
			if err := tx.Unscoped().Model(&Token{}).Where("id = ?", id).Count(&count).Error; err != nil {
				return err
			}
			if count != 0 {
				continue
			}
			key, err := common.GenerateKey()
			if err != nil {
				return err
			}
			token.Key, token.Status, token.Name = key, common.TokenStatusDisabled, "Deleted workspace key"
			token.DeletedAt = gorm.DeletedAt{Time: time.Now(), Valid: true}
			if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&token).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

func FindWorkspaceMembership(userID int) (*WorkspaceMember, *WorkspaceTeam, error) {
	var member WorkspaceMember
	if err := DB.First(&member, "user_id = ?", userID).Error; err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, nil, nil
		}
		return nil, nil, err
	}
	var team WorkspaceTeam
	if err := DB.First(&team, member.TeamID).Error; err != nil {
		return nil, nil, err
	}
	return &member, &team, nil
}

func workspacePersonalToken(tx *gorm.DB, user *User, create bool) (*Token, error) {
	var mapping WorkspacePersonalKey
	err := tx.First(&mapping, "user_id = ?", user.Id).Error
	if err == nil {
		var token Token
		if err := tx.First(&token, "id = ? AND user_id = ? AND workspace_user_id = 0", mapping.TokenID, user.Id).Error; err == nil {
			return &token, nil
		} else if !errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, err
		}
		if !create {
			return nil, nil
		}
		if err := tx.Delete(&mapping).Error; err != nil {
			return nil, err
		}
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, err
	}
	if !create {
		return nil, nil
	}
	// Reuse a compatible existing key without modifying legacy credentials.
	var token Token
	err = tx.Where("user_id = ? AND workspace_user_id = 0 AND status = ? AND unlimited_quota = ? AND model_limits_enabled = ? AND expired_time = -1 AND (allow_ips IS NULL OR allow_ips = '')", user.Id, common.TokenStatusEnabled, true, false).Where(clause.Eq{Column: "group", Value: ""}).Order("id").First(&token).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		key, keyErr := common.GenerateKey()
		if keyErr != nil {
			return nil, keyErr
		}
		token = Token{UserId: user.Id, Key: key, Name: "RealYu API", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, CreatedTime: common.GetTimestamp(), AccessedTime: common.GetTimestamp()}
		if err := tx.Create(&token).Error; err != nil {
			return nil, err
		}
	} else if err != nil {
		return nil, err
	}
	if err := tx.Create(&WorkspacePersonalKey{UserID: user.Id, TokenID: token.Id}).Error; err != nil {
		return nil, err
	}
	return &token, nil
}

// GetWorkspaceKey preserves the old membership-based API for installed clients.
func GetWorkspaceKey(userID int, create bool) (*Token, error) {
	return GetWorkspaceKeyForScope(userID, "current", create)
}

func GetPlaygroundKey(userID int, scope string) (*Token, error) {
	return GetWorkspaceKeyForScope(userID, scope, false)
}

func GetWorkspaceKeyForScope(userID int, scope string, create bool) (*Token, error) {
	if scope == "" {
		scope = "current"
	}
	if scope != "current" && scope != "personal" && scope != "team" {
		return nil, ErrWorkspaceAccess
	}
	var token *Token
	err := DB.Transaction(func(tx *gorm.DB) error {
		var user User
		if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
			return err
		}
		var member WorkspaceMember
		err := tx.First(&member, "user_id = ?", userID).Error
		if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if scope != "personal" && err == nil {
			var team WorkspaceTeam
			if err := tx.First(&team, member.TeamID).Error; err != nil {
				return err
			}
			token = &Token{}
			return tx.First(token, "id = ? AND user_id = ? AND workspace_user_id = ?", member.TokenID, team.OwnerUserID, userID).Error
		}
		if scope == "team" {
			return ErrWorkspaceAccess
		}
		token, err = workspacePersonalToken(tx, &user, create)
		return err
	})
	return token, err
}

func CreateWorkspaceTeam(userID int, name string) (*WorkspaceTeam, error) {
	var team *WorkspaceTeam
	err := DB.Transaction(func(tx *gorm.DB) error {
		var err error
		team, err = CreateWorkspaceTeamTx(tx, userID, name)
		return err
	})
	return team, err
}

// CreateWorkspaceTeamTx is internal provisioning for a confirmed team purchase
// or an explicit migration. Public creation is forbidden for every user role.
// Callers that move a member must lock their old team before locking the user.
func CreateWorkspaceTeamTx(tx *gorm.DB, userID int, name string) (*WorkspaceTeam, error) {
	if tx == nil || userID <= 0 {
		return nil, ErrWorkspaceAccess
	}
	team := &WorkspaceTeam{OwnerUserID: userID, Name: name, CreatedAt: common.GetTimestamp(), FundingVersion: 1}
	var user User
	if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
		return nil, err
	}
	var count int64
	if err := tx.Model(&WorkspaceMember{}).Where("user_id = ?", userID).Count(&count).Error; err != nil {
		return nil, err
	}
	if count > 0 {
		return nil, ErrWorkspaceExists
	}
	if _, err := workspacePersonalToken(tx, &user, true); err != nil {
		return nil, err
	}
	if err := tx.Create(team).Error; err != nil {
		return nil, err
	}
	if err := tx.Create(&WorkspaceTeamAccount{TeamID: team.ID, OwnerUserID: userID}).Error; err != nil {
		return nil, err
	}
	key, err := common.GenerateKey()
	if err != nil {
		return nil, err
	}
	token := Token{UserId: userID, WorkspaceUserID: userID, Key: key, Name: "Team API", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, CreatedTime: common.GetTimestamp()}
	if err := tx.Create(&token).Error; err != nil {
		return nil, err
	}
	err = tx.Create(&WorkspaceMember{UserID: userID, TeamID: team.ID, TokenID: token.Id, Status: common.UserStatusEnabled, CreatedAt: team.CreatedAt}).Error
	return team, err
}

func CreateWorkspaceInvite(ownerID int) (string, int64, error) {
	return GetWorkspaceInvite(ownerID, false)
}

// The owner can retrieve a reusable invitation. Zero expiry means permanent;
// legacy hashed, expiring invitations retain their original one-use semantics.
func GetWorkspaceInvite(ownerID int, rotate bool) (string, int64, error) {
	var code string
	err := DB.Transaction(func(tx *gorm.DB) error {
		var team WorkspaceTeam
		if err := lockForUpdate(tx).First(&team, "owner_user_id = ?", ownerID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if !rotate {
			var invite WorkspaceInvite
			err := tx.First(&invite, "team_id = ? AND expires_at = 0 AND code <> ''", team.ID).Error
			if err == nil {
				code = invite.Code
				return nil
			}
			if !errors.Is(err, gorm.ErrRecordNotFound) {
				return err
			}
		}
		var err error
		code, err = common.GenerateKey()
		if err != nil {
			return err
		}
		hash := sha256.Sum256([]byte(code))
		if err := tx.Where("team_id = ?", team.ID).Delete(&WorkspaceInvite{}).Error; err != nil {
			return err
		}
		return tx.Create(&WorkspaceInvite{Hash: hex.EncodeToString(hash[:]), Code: code, TeamID: team.ID}).Error
	})
	return code, 0, err
}

func JoinWorkspaceTeam(userID int, code string) error {
	if len(code) < 32 || len(code) > 128 {
		return ErrWorkspaceInvite
	}
	hash := sha256.Sum256([]byte(code))
	var candidate WorkspaceInvite
	if err := DB.First(&candidate, "hash = ?", hex.EncodeToString(hash[:])).Error; err != nil {
		return ErrWorkspaceInvite
	}
	var previous WorkspaceMember
	if err := DB.First(&previous, "user_id = ?", userID).Error; err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		return err
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		// Both teams precede the user lock. Stable ordering permits two members
		// to change teams in opposite directions without reversing team locks.
		teamIDs := []int{candidate.TeamID}
		if previous.TeamID > 0 && previous.TeamID != candidate.TeamID {
			teamIDs = append(teamIDs, previous.TeamID)
		}
		sort.Ints(teamIDs)
		var team WorkspaceTeam
		for _, teamID := range teamIDs {
			var locked WorkspaceTeam
			if err := lockForUpdate(tx).First(&locked, teamID).Error; err != nil {
				return ErrWorkspaceInvite
			}
			if teamID == candidate.TeamID {
				team = locked
			}
		}
		var user User
		if err := lockForUpdate(tx).First(&user, userID).Error; err != nil {
			return err
		}
		var existing int64
		if err := tx.Model(&WorkspaceTeam{}).Where("owner_user_id = ?", userID).Count(&existing).Error; err != nil {
			return err
		}
		if existing != 0 {
			return ErrWorkspaceOwnerJoin
		}
		var membership WorkspaceMember
		if err := tx.First(&membership, "user_id = ?", userID).Error; err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		// A concurrent leave/join wins normally; this stale attempt must not
		// remove its new membership using an earlier snapshot.
		if membership.TeamID != previous.TeamID || membership.TokenID != previous.TokenID || membership.TeamID == team.ID {
			return ErrWorkspaceExists
		}
		var invite WorkspaceInvite
		if err := lockForUpdate(tx).First(&invite, "hash = ? AND team_id = ? AND used_by = 0 AND (expires_at > ? OR (expires_at = 0 AND code <> ''))", hex.EncodeToString(hash[:]), team.ID, common.GetTimestamp()).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return ErrWorkspaceInvite
			}
			return err
		}
		var owner User
		if err := tx.First(&owner, "id = ? AND status = ?", team.OwnerUserID, common.UserStatusEnabled).Error; err != nil {
			return ErrWorkspaceInvite
		}
		if membership.TeamID > 0 {
			if _, _, err := leaveWorkspaceTeamTx(tx, userID, membership.TeamID, 0, true); err != nil {
				return err
			}
		}
		key, err := common.GenerateKey()
		if err != nil {
			return err
		}
		token := Token{UserId: owner.Id, WorkspaceUserID: userID, Key: key, Name: user.Username, Status: common.TokenStatusEnabled, ExpiredTime: -1, CreatedTime: common.GetTimestamp(), AccessedTime: common.GetTimestamp()}
		if err := tx.Create(&token).Error; err != nil {
			return err
		}
		if err := tx.Create(&WorkspaceMember{UserID: userID, TeamID: team.ID, TokenID: token.Id, Status: common.UserStatusEnabled, CreatedAt: common.GetTimestamp()}).Error; err != nil {
			return err
		}
		if invite.ExpiresAt == 0 {
			return nil
		}
		result := tx.Model(&invite).Where("used_by = 0 AND expires_at > ?", common.GetTimestamp()).Update("used_by", userID)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrWorkspaceInvite
		}
		return nil
	})
}

// Retain revoked token rows for in-flight settlement and historical attribution.
// The creator's key was their personal key, so restore it rather than replacing it.
func LeaveWorkspaceTeam(userID, teamID int) (bool, error) {
	return leaveWorkspaceTeam(userID, teamID, 0)
}

// LeaveWorkspaceTeamTx allows a paid provisioning transaction to move an
// ordinary member atomically. It never invalidates caches before commit; the
// caller must invalidate the returned token keys after a successful commit.
func LeaveWorkspaceTeamTx(tx *gorm.DB, userID, teamID int) (bool, []string, error) {
	return leaveWorkspaceTeamTx(tx, userID, teamID, 0, false)
}

// Future entitlements also protect a team from dissolution. Legacy personal
// subscriptions may still fund the shared team and must remain attached until
// migration or expiry; isolated teams do not depend on personal subscriptions.
func HasUnexpiredWorkspaceTeamSubscription(team *WorkspaceTeam) (bool, error) {
	return hasUnexpiredWorkspaceTeamSubscriptionTx(DB, team)
}

func hasUnexpiredWorkspaceTeamSubscriptionTx(tx *gorm.DB, team *WorkspaceTeam) (bool, error) {
	if team == nil || team.ID <= 0 || team.OwnerUserID <= 0 {
		return false, ErrWorkspaceAccess
	}
	query := tx.Model(&UserSubscription{}).Where("status = ? AND end_time > ?", "active", getDBTimestampTx(tx))
	if team.FundingVersion == 0 {
		query = query.Where("workspace_team_id = ? OR (workspace_team_id = 0 AND user_id = ?)", team.ID, team.OwnerUserID)
	} else {
		query = query.Where("workspace_team_id = ?", team.ID)
	}
	var count int64
	if err := query.Count(&count).Error; err != nil {
		return false, err
	}
	return count > 0, nil
}

// Owner authorization and revocation share the membership transaction.
func RemoveWorkspaceMember(ownerID, teamID, memberID int) error {
	if ownerID <= 0 || memberID <= 0 || ownerID == memberID {
		return ErrWorkspaceAccess
	}
	_, err := leaveWorkspaceTeam(memberID, teamID, ownerID)
	return err
}

func leaveWorkspaceTeam(userID, teamID, managingOwnerID int) (bool, error) {
	dissolved := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var err error
		dissolved, _, err = leaveWorkspaceTeamTx(tx, userID, teamID, managingOwnerID, true)
		return err
	})
	return dissolved && err == nil, err
}

func leaveWorkspaceTeamTx(tx *gorm.DB, userID, teamID, managingOwnerID int, invalidateBeforeMutation bool) (bool, []string, error) {
	dissolved := false
	var tokenKeys []string
	if tx == nil || teamID <= 0 {
		return false, nil, ErrWorkspaceAccess
	}
	err := func() error {
		var team WorkspaceTeam
		if err := lockForUpdate(tx).First(&team, teamID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if managingOwnerID > 0 && team.OwnerUserID != managingOwnerID {
			return ErrWorkspaceAccess
		}
		if team.FundingVersion > 0 {
			// Funding mutations hold account before subscription/token rows.
			// Revocation and dissolution must use that order as well.
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ?", teamID).Error; err != nil {
				return err
			}
		}
		var member WorkspaceMember
		if err := lockForUpdate(tx).First(&member, "team_id = ? AND user_id = ?", teamID, userID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		dissolved = team.OwnerUserID == userID
		members := []WorkspaceMember{member}
		if dissolved {
			// Closing the team revokes access without changing subscription or
			// billing history. Unused allowance is not refunded or transferred.
			if err := tx.Where("team_id = ?", teamID).Order("user_id").Find(&members).Error; err != nil {
				return err
			}
		}
		for _, departing := range members {
			var user User
			userErr := lockForUpdate(tx.Unscoped()).First(&user, departing.UserID).Error
			if userErr != nil && !errors.Is(userErr, gorm.ErrRecordNotFound) {
				return userErr
			}
			var token Token
			err := lockForUpdate(tx.Unscoped()).First(&token, departing.TokenID).Error
			if errors.Is(err, gorm.ErrRecordNotFound) {
				// Legacy hard deletion removed this key. Keep a disabled tombstone
				// so old log IDs cannot be reassigned to a future credential.
				key, keyErr := common.GenerateKey()
				if keyErr != nil {
					return keyErr
				}
				token = Token{Id: departing.TokenID, UserId: team.OwnerUserID, WorkspaceUserID: departing.UserID, Key: key, Status: common.TokenStatusDisabled, Name: "Deleted workspace key", DeletedAt: gorm.DeletedAt{Time: time.Now(), Valid: true}}
				if err = tx.Create(&token).Error; err != nil {
					return err
				}
			} else if err != nil {
				return err
			}
			if token.UserId != team.OwnerUserID || token.WorkspaceUserID != departing.UserID {
				return ErrWorkspaceAccess
			}
			if invalidateBeforeMutation {
				if err := invalidateTokenCacheForMutation(token.Key); err != nil {
					return err
				}
			}
			tokenKeys = append(tokenKeys, token.Key)
			values := map[string]any{"status": common.TokenStatusDisabled}
			if departing.UserID == team.OwnerUserID && team.FundingVersion == 0 {
				values = map[string]any{"workspace_user_id": 0}
			}
			if err := tx.Unscoped().Model(&token).Updates(values).Error; err != nil {
				return err
			}
			if err := tx.Delete(&departing).Error; err != nil {
				return err
			}
			if userErr == nil && !user.DeletedAt.Valid {
				if _, err := workspacePersonalToken(tx, &user, true); err != nil {
					return err
				}
			}
		}
		if dissolved {
			if err := tx.Where("team_id = ?", teamID).Delete(&WorkspaceInvite{}).Error; err != nil {
				return err
			}
			if team.FundingVersion > 0 {
				if err := tx.Model(&WorkspaceTeamAccount{}).Where("team_id = ?", team.ID).Update("closed_at", common.GetTimestamp()).Error; err != nil {
					return err
				}
			}
			return tx.Delete(&team).Error
		}
		return nil
	}()
	return dissolved && err == nil, tokenKeys, err
}

func GetOwnedWorkspaceMemberToken(ownerID, memberID int) (*Token, error) {
	var team WorkspaceTeam
	if err := DB.First(&team, "owner_user_id = ?", ownerID).Error; err != nil {
		return nil, ErrWorkspaceAccess
	}
	var member WorkspaceMember
	if err := DB.First(&member, "user_id = ? AND team_id = ?", memberID, team.ID).Error; err != nil {
		return nil, ErrWorkspaceAccess
	}
	var token Token
	if err := DB.First(&token, "id = ? AND user_id = ? AND workspace_user_id = ?", member.TokenID, ownerID, memberID).Error; err != nil {
		return nil, ErrWorkspaceAccess
	}
	return &token, nil
}

func UpdateWorkspaceMember(ownerID, memberID int, quota *int, status *int, displayName *string, management ...*WorkspaceTeamManagement) error {
	if ownerID == memberID && (quota != nil || status != nil) {
		return errors.New("团队创建者使用共享余额，无需分配成员额度")
	}
	return DB.Transaction(func(tx *gorm.DB) error {
		var team WorkspaceTeam
		query := lockForUpdate(tx).Where("owner_user_id = ?", ownerID)
		if len(management) > 0 && management[0] != nil {
			query = query.Where("id = ?", management[0].TeamID)
		}
		if err := query.First(&team).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if len(management) > 0 && management[0] != nil {
			if err := management[0].authorize(tx, &team, memberID); err != nil {
				return err
			}
		}
		if team.FundingVersion > 0 {
			var account WorkspaceTeamAccount
			if err := lockForUpdate(tx).First(&account, "team_id = ?", team.ID).Error; err != nil {
				return err
			}
		}
		var member WorkspaceMember
		if err := lockForUpdate(tx).First(&member, "team_id = ? AND user_id = ?", team.ID, memberID).Error; err != nil {
			return ErrWorkspaceAccess
		}
		if displayName != nil {
			if err := UpdateUserDisplayName(tx, memberID, *displayName); err != nil {
				return err
			}
		}
		if quota == nil && status == nil {
			return nil
		}
		var token Token
		if err := lockForUpdate(tx).First(&token, "id = ? AND user_id = ?", member.TokenID, ownerID).Error; err != nil {
			return err
		}
		if err := invalidateTokenCacheForMutation(token.Key); err != nil {
			return err
		}
		values := map[string]any{}
		if quota != nil {
			values["remain_quota"] = *quota
			if team.FundingVersion > 0 {
				if err := tx.Model(&member).Update("weekly_quota", *quota).Error; err != nil {
					return err
				}
			}
		}
		if status != nil {
			member.Status = *status
			if err := tx.Model(&member).Update("status", *status).Error; err != nil {
				return err
			}
		}
		values["status"] = common.TokenStatusEnabled
		if member.Status != common.UserStatusEnabled {
			values["status"] = common.TokenStatusDisabled
		}
		// Always preserve the native limited-token billing invariant.
		values["unlimited_quota"] = false
		return tx.Model(&token).Updates(values).Error
	})
}

func RotateWorkspaceToken(token *Token) error {
	key, err := common.GenerateKey()
	if err != nil {
		return err
	}
	if err := invalidateTokenCacheForMutation(token.Key); err != nil {
		return err
	}
	result := DB.Model(&Token{}).Where("id = ? AND user_id = ? AND "+commonKeyCol+" = ?", token.Id, token.UserId, token.Key).Update("key", key)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("密钥已被更新，请刷新后重试")
	}
	token.Key = key
	return nil
}

// The native UserId remains the billing owner; WorkspaceUserID is solely the
// credential holder. Check it even if the owner renames or edits a native token.
func ValidateWorkspaceToken(token *Token) error {
	token.WorkspaceTeamID = 0
	token.FundingTeamID = 0
	if token.WorkspaceUserID == 0 {
		return nil
	}
	var member WorkspaceMember
	if err := DB.First(&member, "user_id = ? AND token_id = ? AND status = ?", token.WorkspaceUserID, token.Id, common.UserStatusEnabled).Error; err != nil {
		return ErrTokenInvalid
	}
	var team WorkspaceTeam
	if err := DB.First(&team, "id = ? AND owner_user_id = ?", member.TeamID, token.UserId).Error; err != nil {
		return ErrTokenInvalid
	}
	var count int64
	if err := DB.Model(&User{}).Where("id = ? AND status = ?", member.UserID, common.UserStatusEnabled).Count(&count).Error; err != nil {
		return err
	}
	if count != 1 || (member.UserID != team.OwnerUserID && token.UnlimitedQuota) {
		return ErrTokenInvalid
	}
	token.WorkspaceTeamID = team.ID
	if team.FundingVersion > 0 {
		token.FundingTeamID = team.ID
	}
	return nil
}

func WorkspaceKeyText(token *Token) string {
	return "sk-" + strings.TrimPrefix(token.Key, "sk-")
}

type WorkspaceUsage struct {
	Requests         int64 `json:"requests"`
	PromptTokens     int64 `json:"prompt_tokens"`
	CompletionTokens int64 `json:"completion_tokens"`
	UsedQuota        int64 `json:"-"`
}

// Summaries include retired keys but only consumption since this team began.
// The funding wallet's lifetime spending is a different metric.
func GetWorkspaceTeamUsage(team *WorkspaceTeam) (WorkspaceUsage, map[int]WorkspaceUsage, error) {
	total := WorkspaceUsage{}
	byMember := make(map[int]WorkspaceUsage)
	var tokens []Token
	if err := DB.Unscoped().Select("id, workspace_user_id").Where("user_id = ? AND workspace_user_id > 0", team.OwnerUserID).Find(&tokens).Error; err != nil {
		return total, byMember, err
	}
	ids := make([]int, 0, len(tokens))
	holders := make(map[int]int)
	for _, token := range tokens {
		ids = append(ids, token.Id)
		holders[token.Id] = token.WorkspaceUserID
	}
	if len(ids) == 0 {
		return total, byMember, nil
	}
	var rows []struct {
		TokenID int
		WorkspaceUsage
	}
	err := workspaceTeamLogScope(LOG_DB.Model(&Log{}), team).Where("type = ? AND user_id = ? AND token_id IN ?", LogTypeConsume, team.OwnerUserID, ids).
		Select("token_id, COUNT(*) AS requests, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens, COALESCE(SUM(quota), 0) AS used_quota").Group("token_id").Scan(&rows).Error
	for _, row := range rows {
		usage := byMember[holders[row.TokenID]]
		usage.Requests += row.Requests
		usage.PromptTokens += row.PromptTokens
		usage.CompletionTokens += row.CompletionTokens
		usage.UsedQuota += row.UsedQuota
		byMember[holders[row.TokenID]] = usage
		total.Requests += row.Requests
		total.PromptTokens += row.PromptTokens
		total.CompletionTokens += row.CompletionTokens
		total.UsedQuota += row.UsedQuota
	}
	return total, byMember, err
}

func workspaceTeamLogScope(query *gorm.DB, team *WorkspaceTeam) *gorm.DB {
	// 0 is a historical record without attribution; -1 is explicitly personal.
	// New team requests retain the team authenticated before streaming began.
	return query.Where("workspace_team_id = ? OR (workspace_team_id = 0 AND created_at >= ?)", team.ID, team.CreatedAt)
}

// Historical reports retain member attribution after account deletion. This
// reader is never used for authentication and selects no credential fields.
func GetWorkspaceAccountSnapshot(userID int) (*User, error) {
	var user User
	err := DB.Unscoped().Select("id, username, display_name, status, quota, deleted_at").First(&user, userID).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return &User{Id: userID, Username: "deleted-user", DisplayName: "已删除用户", Status: common.UserStatusDisabled}, nil
	}
	if err != nil {
		return nil, err
	}
	if user.DeletedAt.Valid || user.Status != common.UserStatusEnabled {
		user.Status = common.UserStatusDisabled
		user.Quota = 0
	}
	return &user, nil
}

func GetWorkspaceUsage(tokenID int, userID int) (WorkspaceUsage, error) {
	return GetWorkspaceScopedUsage(tokenID, userID, "current")
}

func GetWorkspaceScopedUsage(tokenID int, userID int, scopeName string) (WorkspaceUsage, error) {
	var result WorkspaceUsage
	query := LOG_DB.Model(&Log{}).Where("type = ?", LogTypeConsume)
	if tokenID > 0 {
		query = query.Where("token_id = ?", tokenID)
	} else {
		scope, err := ResolveWorkspaceUsageScope(userID)
		if err != nil {
			return result, err
		}
		query = scope.Apply(query)
	}
	if scopeName == "personal" {
		query = query.Where("workspace_team_id <= 0")
	}
	if scopeName == "team" {
		query = query.Where("workspace_team_id >= 0")
	}
	err := query.Select("COUNT(*) AS requests, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens, COALESCE(SUM(quota), 0) AS used_quota").Scan(&result).Error
	return result, err
}

func GetWorkspaceUsageLogs(tokenID int, userID int) ([]Log, int64, error) {
	return GetWorkspaceScopedUsageLogs(tokenID, userID, "current")
}

func GetWorkspaceScopedUsageLogs(tokenID int, userID int, scopeName string) ([]Log, int64, error) {
	logs := []Log{}
	var count int64
	query := LOG_DB.Model(&Log{}).Where("type = ?", LogTypeConsume)
	if tokenID > 0 {
		query = query.Where("token_id = ?", tokenID)
	} else {
		scope, err := ResolveWorkspaceUsageScope(userID)
		if err != nil {
			return nil, 0, err
		}
		query = scope.Apply(query)
	}
	if scopeName == "personal" {
		query = query.Where("workspace_team_id <= 0")
	}
	if scopeName == "team" {
		query = query.Where("workspace_team_id >= 0")
	}
	if err := query.Count(&count).Error; err != nil {
		return nil, 0, err
	}
	err := query.Select("id, created_at, model_name, quota, prompt_tokens, completion_tokens").Order("id DESC").Limit(100).Find(&logs).Error
	return logs, count, err
}

// Team analytics are scoped on the server to the creator's team credentials.
// Retired member keys remain attributable. Personal activity before creation
// and activity in another owner's team are never included.
func GetWorkspaceTeamTrends(ownerID int, start, end int64, expectedTeamID ...int) ([]QuotaData, error) {
	var team WorkspaceTeam
	query := DB.Where("owner_user_id = ?", ownerID)
	if len(expectedTeamID) > 0 {
		query = query.Where("id = ?", expectedTeamID[0])
	}
	if err := query.First(&team).Error; err != nil {
		return nil, ErrWorkspaceAccess
	}
	var tokens []Token
	if err := DB.Unscoped().Select("id, workspace_user_id").Where("user_id = ? AND workspace_user_id > 0", ownerID).Find(&tokens).Error; err != nil {
		return nil, err
	}
	ids := make([]int, 0, len(tokens))
	holders := make(map[int]int)
	names := make(map[int]string)
	for _, token := range tokens {
		ids = append(ids, token.Id)
		holders[token.Id] = token.WorkspaceUserID
		if _, ok := names[token.WorkspaceUserID]; !ok {
			user, err := GetWorkspaceAccountSnapshot(token.WorkspaceUserID)
			if err != nil {
				return nil, err
			}
			name := user.DisplayName
			if name == "" {
				name = user.Username
			}
			names[token.WorkspaceUserID] = name
		}
	}
	rows := []QuotaData{}
	if len(ids) == 0 {
		return rows, nil
	}
	if start < team.CreatedAt {
		start = team.CreatedAt
	}
	// Modulo on integer timestamps works on SQLite, MySQL and PostgreSQL.
	bucket := "(created_at - (created_at % 3600))"
	err := workspaceTeamLogScope(LOG_DB.Model(&Log{}), &team).Where("type = ? AND user_id = ? AND token_id IN ? AND created_at >= ? AND created_at <= ?", LogTypeConsume, ownerID, ids, start, end).
		Select("token_id, model_name, " + bucket + " AS created_at, COUNT(*) AS count, SUM(quota) AS quota, SUM(prompt_tokens + completion_tokens) AS token_used").
		Group("token_id, model_name, " + bucket).Order("created_at").Scan(&rows).Error
	for i := range rows {
		rows[i].UserID = holders[rows[i].TokenID]
		rows[i].Username = names[rows[i].UserID]
		rows[i].TokenID = 0
	}
	return rows, err
}

// WorkspaceUsageScope is resolved from trusted membership records in the main
// database, then applied to either main or separately configured log databases.
// Personal usage includes a member's own pre-team keys but excludes team-mates
// paid for by this user's wallet. No user-supplied filter can widen this scope.
type WorkspaceUsageScope struct {
	UserID              int
	MemberTokenIDs      []int
	OtherMemberTokenIDs []int
	Team                *WorkspaceTeam
}

// Administrator log filters resolve caller IDs against historical token
// ownership in the main database, including with a separate log database.
func ResolveAdminWorkspaceLogScope(memberID, teamID int) (*WorkspaceUsageScope, error) {
	if teamID == 0 {
		return ResolveWorkspaceUsageScope(memberID)
	}
	scope := &WorkspaceUsageScope{UserID: memberID, Team: &WorkspaceTeam{}}
	if err := DB.First(scope.Team, teamID).Error; err != nil {
		return nil, err
	}
	query := DB.Unscoped().Model(&Token{}).Where("user_id = ? AND workspace_user_id > 0", scope.Team.OwnerUserID)
	if memberID > 0 {
		query = query.Where("workspace_user_id = ?", memberID)
	}
	if err := query.Pluck("id", &scope.MemberTokenIDs).Error; err != nil {
		return nil, err
	}
	return scope, nil
}

func ResolveWorkspaceUsageScope(userID int) (*WorkspaceUsageScope, error) {
	scope := &WorkspaceUsageScope{UserID: userID}
	if err := DB.Unscoped().Model(&Token{}).Where("workspace_user_id = ? AND user_id <> ?", userID, userID).Pluck("id", &scope.MemberTokenIDs).Error; err != nil {
		return nil, err
	}
	err := DB.Unscoped().Model(&Token{}).Where("user_id = ? AND workspace_user_id > 0 AND workspace_user_id <> ?", userID, userID).Pluck("id", &scope.OtherMemberTokenIDs).Error
	return scope, err
}

func (scope *WorkspaceUsageScope) Apply(query *gorm.DB) *gorm.DB {
	if scope == nil {
		return query
	}
	if scope.Team != nil {
		return workspaceTeamLogScope(query, scope.Team).Where("user_id = ? AND token_id IN ?", scope.Team.OwnerUserID, scope.MemberTokenIDs)
	}
	condition := "user_id = ?"
	values := []any{scope.UserID}
	if len(scope.OtherMemberTokenIDs) > 0 {
		condition += " AND token_id NOT IN ?"
		values = append(values, scope.OtherMemberTokenIDs)
	}
	if len(scope.MemberTokenIDs) > 0 {
		condition = "(" + condition + ") OR token_id IN ?"
		values = append(values, scope.MemberTokenIDs)
	}
	return query.Where("("+condition+")", values...)
}
