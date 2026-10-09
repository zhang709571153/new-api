package controller

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"strconv"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

// Workspace management intentionally accepts live dashboard sessions only.
// Relay keys and legacy PATs cannot create invitations or retrieve credentials.
// Authentication uses an explicit Authorization header, never a cookie alone.
func WorkspaceSessionRequired(c *gin.Context) {
	if _, ok := middleware.GetSessionAuthIdentity(c); !ok {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "message": "请登录网站后操作"})
		return
	}
	if c.GetHeader("Sec-Fetch-Site") == "cross-site" {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "message": "不允许跨站请求"})
		return
	}
	// Read the small management payload before acting. Unused POST bodies
	// arriving after a Connection: close response can otherwise reset the
	// Windows TCP connection and hide a successful key/invite operation.
	if c.Request.Body != nil {
		payload, err := io.ReadAll(http.MaxBytesReader(c.Writer, c.Request.Body, 16<<10))
		_ = c.Request.Body.Close()
		if err != nil {
			c.AbortWithStatusJSON(http.StatusRequestEntityTooLarge, gin.H{"success": false, "message": "请求内容过大或无法读取"})
			return
		}
		c.Request.Body = io.NopCloser(bytes.NewReader(payload))
	}
	c.Header("Cache-Control", "no-store")
	c.Next()
}

func workspaceResult(c *gin.Context, result any, err error, action string, targetID int) {
	if action != "" {
		recordUserSecurityAudit(c, c.GetInt("id"), action, map[string]any{"success": err == nil, "target_user_id": targetID})
	}
	if err != nil {
		message := "操作失败，请检查输入或刷新后重试"
		for _, expected := range []error{model.ErrWorkspaceAccess, model.ErrWorkspaceExists, model.ErrWorkspaceInvite, model.ErrWorkspaceOwnerJoin} {
			if errors.Is(err, expected) {
				message = expected.Error()
			}
		}
		if errors.Is(err, gorm.ErrRecordNotFound) {
			message = "尚未创建 API 密钥，或该记录已不存在"
		}
		// SQL errors may contain credentials. Never return or audit raw errors.
		common.ApiError(c, errors.New(message))
		return
	}
	common.ApiSuccess(c, result)
}

func GetWorkspace(c *gin.Context) {
	result, err := service.GetWorkspaceSummaryForScope(c.GetInt("id"), c.Query("scope"))
	workspaceResult(c, result, err, "", 0)
}

func CreateWorkspaceKey(c *gin.Context) {
	token, err := model.GetWorkspaceKeyForScope(c.GetInt("id"), c.Query("scope"), true)
	var result *service.WorkspaceKey
	if err == nil {
		result = &service.WorkspaceKey{ID: token.Id, MaskedKey: "sk-" + token.GetMaskedKey(), Status: token.Status}
	}
	workspaceResult(c, result, err, "workspace.key.create", c.GetInt("id"))
}

func RevealWorkspaceKey(c *gin.Context) {
	workspaceKeyAction(c, false)
}

func RotateWorkspaceKey(c *gin.Context) {
	workspaceKeyAction(c, true)
}

func workspaceKeyAction(c *gin.Context, rotate bool) {
	userID := c.GetInt("id")
	targetID := userID
	var token *model.Token
	var err error
	if c.Param("id") != "" {
		targetID, err = strconv.Atoi(c.Param("id"))
		if err == nil && targetID > 0 {
			token, err = model.GetOwnedWorkspaceMemberToken(userID, targetID)
		} else {
			err = model.ErrWorkspaceAccess
		}
	} else {
		token, err = model.GetWorkspaceKeyForScope(userID, c.Query("scope"), false)
		if err == nil && token == nil {
			err = gorm.ErrRecordNotFound
		}
	}
	action := "workspace.key.reveal"
	if rotate {
		action = "workspace.key.rotate"
		if err == nil {
			err = model.RotateWorkspaceToken(token)
		}
	}
	var result any
	if err == nil {
		result = gin.H{"api_key": model.WorkspaceKeyText(token)}
	}
	workspaceResult(c, result, err, action, targetID)
}

func CreateWorkspaceTeam(c *gin.Context) {
	// A successful team-plan purchase provisions the owner atomically. Keep the
	// old route as an explicit rejection, including for administrators.
	recordUserSecurityAudit(c, c.GetInt("id"), "workspace.team.create", map[string]any{"success": false, "target_user_id": c.GetInt("id")})
	c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"success": false, "message": "请先购买团队套餐，购买成功后自动开通团队"})
}

func GetWorkspaceTeam(c *gin.Context) {
	result, err := service.GetWorkspaceTeamView(c.GetInt("id"), 0)
	workspaceResult(c, result, err, "", 0)
}

func CreateWorkspaceInvite(c *gin.Context) {
	code, expiresAt, err := model.CreateWorkspaceInvite(c.GetInt("id"))
	workspaceResult(c, gin.H{"code": code, "expires_at": expiresAt}, err, "workspace.team.invite", 0)
}

func RotateWorkspaceInvite(c *gin.Context) {
	code, expiresAt, err := model.GetWorkspaceInvite(c.GetInt("id"), true)
	workspaceResult(c, gin.H{"code": code, "expires_at": expiresAt}, err, "workspace.team.invite.rotate", 0)
}

func LeaveWorkspaceTeam(c *gin.Context) {
	var request struct {
		TeamID int `json:"team_id"`
	}
	err := common.DecodeJson(c.Request.Body, &request)
	dissolved := false
	if err == nil {
		dissolved, err = model.LeaveWorkspaceTeam(c.GetInt("id"), request.TeamID)
	}
	workspaceResult(c, gin.H{"dissolved": dissolved}, err, "workspace.team.leave", c.GetInt("id"))
}

func GetWorkspaceTeamTrends(c *gin.Context) {
	start, end, ok := parseFlowQuotaTimeRange(c)
	if !ok {
		return
	}
	if end-start > 30*24*3600 {
		common.ApiError(c, errors.New("请选择不超过 30 天的有效时间范围"))
		return
	}
	data, err := model.GetWorkspaceTeamTrends(c.GetInt("id"), start, end)
	workspaceResult(c, data, err, "", 0)
}

func GetWorkspaceTeamLogs(c *gin.Context) {
	var filter model.WorkspaceTeamLogFilter
	if err := c.ShouldBindQuery(&filter); err != nil || (filter.EndTimestamp > 0 && filter.StartTimestamp > filter.EndTimestamp) {
		common.ApiErrorMsg(c, "请选择有效的请求筛选条件")
		return
	}
	page := common.GetPageQuery(c)
	logs, members, total, err := model.GetWorkspaceTeamLogs(c.GetInt("id"), filter, page.GetStartIdx(), page.GetPageSize())
	page.SetTotal(int(total))
	page.SetItems(logs)
	workspaceResult(c, struct {
		*common.PageInfo
		Members []model.WorkspaceLogMember `json:"members"`
	}{page, members}, err, "", 0)
}

func GetWorkspaceTeamLogStats(c *gin.Context) {
	var filter model.WorkspaceTeamLogFilter
	if err := c.ShouldBindQuery(&filter); err != nil || (filter.EndTimestamp > 0 && filter.StartTimestamp > filter.EndTimestamp) {
		common.ApiErrorMsg(c, "请选择有效的请求筛选条件")
		return
	}
	stat, err := model.GetWorkspaceTeamLogStats(c.GetInt("id"), filter)
	workspaceResult(c, stat, err, "", 0)
}

func JoinWorkspaceTeam(c *gin.Context) {
	var request struct {
		Code string `json:"code"`
	}
	if err := common.DecodeJson(c.Request.Body, &request); err != nil {
		workspaceResult(c, nil, err, "workspace.team.join", c.GetInt("id"))
		return
	}
	err := model.JoinWorkspaceTeam(c.GetInt("id"), request.Code)
	workspaceResult(c, nil, err, "workspace.team.join", c.GetInt("id"))
}

func UpdateWorkspaceMember(c *gin.Context) {
	memberID, err := strconv.Atoi(c.Param("id"))
	if err != nil || memberID <= 0 {
		workspaceResult(c, nil, model.ErrWorkspaceAccess, "workspace.member.update", memberID)
		return
	}
	var request service.WorkspaceMemberUpdate
	if err = common.DecodeJson(c.Request.Body, &request); err == nil {
		err = service.UpdateWorkspaceMember(c.GetInt("id"), memberID, request)
	}
	workspaceResult(c, nil, err, "workspace.member.update", memberID)
}

func RemoveWorkspaceMember(c *gin.Context) {
	memberID, err := strconv.Atoi(c.Param("id"))
	var request struct {
		TeamID int `json:"team_id"`
	}
	if err == nil {
		err = common.DecodeJson(c.Request.Body, &request)
	}
	if err == nil {
		err = model.RemoveWorkspaceMember(c.GetInt("id"), request.TeamID, memberID)
	}
	workspaceResult(c, nil, err, "workspace.member.remove", memberID)
}

func GetWorkspaceUsage(c *gin.Context) {
	userID := c.GetInt("id")
	summary, err := service.GetWorkspaceSummaryForScope(userID, c.Query("scope"))
	if err != nil {
		workspaceResult(c, nil, err, "", 0)
		return
	}
	tokenID := 0
	if summary.Scope == "team" && summary.APIKey != nil {
		tokenID = summary.APIKey.ID
	}
	logs, total, err := model.GetWorkspaceScopedUsageLogs(tokenID, userID, summary.Scope)
	items := []gin.H{}
	for _, log := range logs {
		items = append(items, gin.H{"id": log.Id, "created_at": log.CreatedAt, "model_name": log.ModelName, "prompt_tokens": log.PromptTokens, "completion_tokens": log.CompletionTokens, "cost_usd": float64(log.Quota) / common.QuotaPerUnit})
	}
	workspaceResult(c, gin.H{"items": items, "total": total}, err, "", 0)
}

func GetSupplierWorkspaces(c *gin.Context) {
	var teams []model.WorkspaceTeam
	if err := model.DB.Order("id").Find(&teams).Error; err != nil {
		workspaceResult(c, nil, err, "", 0)
		return
	}
	results := []*service.WorkspaceTeamView{}
	for _, team := range teams {
		result, err := service.GetWorkspaceTeamView(c.GetInt("id"), team.ID)
		if err != nil {
			workspaceResult(c, nil, err, "", 0)
			return
		}
		results = append(results, result)
	}
	workspaceResult(c, gin.H{"teams": results}, nil, "", 0)
}
