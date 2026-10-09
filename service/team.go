package service

import (
	"errors"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relaykit/dto"
)

type CreateTeamMemberRequest struct {
	Username    string   `json:"username"`
	DisplayName string   `json:"display_name"`
	Quota       int      `json:"quota"`
	Models      []string `json:"models"`
}

type TeamMemberCredentials struct {
	User    model.TeamMember `json:"user"`
	APIKey  string           `json:"api_key"`
	TokenId int              `json:"token_id"`
}

var teamUsername = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,19}$`)

func ProvisionTeamMember(req CreateTeamMemberRequest) (*TeamMemberCredentials, error) {
	req.Username, req.DisplayName = strings.TrimSpace(req.Username), strings.TrimSpace(req.DisplayName)
	if !teamUsername.MatchString(req.Username) {
		return nil, errors.New("账号须为 2–20 位字母、数字、点、下划线或短横线")
	}
	if req.DisplayName == "" {
		req.DisplayName = req.Username
	}
	if utf8.RuneCountInString(req.DisplayName) > 20 || req.Quota <= 0 || req.Quota > 2_000_000_000 {
		return nil, errors.New("姓名最多 20 字，初始额度须为 1–2000000000 个额度单位")
	}
	if len(req.Models) > 100 {
		return nil, errors.New("最多选择 100 个模型")
	}
	seen := map[string]bool{}
	models := make([]string, 0, len(req.Models))
	for _, name := range req.Models {
		name = strings.TrimSpace(name)
		if name == "" || len(name) > 200 || strings.ContainsAny(name, ",\r\n") {
			return nil, errors.New("模型名称无效")
		}
		if !seen[name] {
			models = append(models, name)
			seen[name] = true
		}
	}
	key, err := common.GenerateKey()
	if err != nil {
		return nil, err
	}
	affCode, err := common.GenerateRandomCharsKey(16)
	if err != nil {
		return nil, err
	}
	// API-only members have no dashboard password. The native login validator
	// rejects passwordless users; no permanent initial password is issued.
	user := &model.User{Username: req.Username, DisplayName: req.DisplayName,
		Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default",
		Quota: req.Quota, AffCode: affCode, AuthVersion: 1}
	user.SetSetting(dto.UserSetting{BillingPreference: "wallet_only"})
	token := &model.Token{Name: req.Username + "-codex", Key: key, Status: common.TokenStatusEnabled,
		CreatedTime: common.GetTimestamp(), AccessedTime: common.GetTimestamp(), ExpiredTime: -1,
		UnlimitedQuota: true, Group: "default", ModelLimitsEnabled: len(models) > 0, ModelLimits: strings.Join(models, ",")}
	if err := model.CreateTeamMember(user, token); err != nil {
		// A database constraint error can include the rejected credential value.
		// Keep credential-bearing SQL errors out of application logs.
		common.SysError("team member provisioning transaction failed")
		return nil, errors.New("创建失败，请检查账号是否已存在")
	}
	return &TeamMemberCredentials{User: model.TeamMember{Id: user.Id, Username: user.Username,
		DisplayName: user.DisplayName, Status: user.Status, Quota: user.Quota},
		APIKey: "sk-" + key, TokenId: token.Id}, nil
}
