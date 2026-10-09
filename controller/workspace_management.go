package controller

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"strconv"
)

func GetAdminWorkspaceTeam(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("team_id"))
	if err != nil || id <= 0 {
		workspaceResult(c, nil, model.ErrWorkspaceAccess, "", 0)
		return
	}
	data, err := service.GetWorkspaceTeamView(c.GetInt("id"), id)
	workspaceResult(c, data, err, "", 0)
}

func AdminManageWorkspaceTeam(c *gin.Context) {
	teamID, err := strconv.Atoi(c.Param("team_id"))
	if err != nil || teamID <= 0 {
		workspaceResult(c, nil, model.ErrWorkspaceAccess, "", 0)
		return
	}
	var team model.WorkspaceTeam
	if err = model.DB.First(&team, teamID).Error; err != nil {
		workspaceResult(c, nil, model.ErrWorkspaceAccess, "", 0)
		return
	}
	management := &model.WorkspaceTeamManagement{ActorID: c.GetInt("id"), TeamID: teamID}
	memberID := 0
	action := "workspace.team.rename"
	if c.Param("id") == "" {
		var request struct {
			Name string `json:"name"`
		}
		if err = common.DecodeJson(c.Request.Body, &request); err == nil {
			err = model.RenameWorkspaceTeam(team.OwnerUserID, teamID, request.Name, management)
		}
	} else {
		memberID, err = strconv.Atoi(c.Param("id"))
		if err != nil || memberID <= 0 {
			err = model.ErrWorkspaceAccess
		} else if c.Request.Method == "DELETE" {
			action = "workspace.member.remove"
			err = model.AdminRemoveWorkspaceMember(management, memberID)
		} else {
			action = "workspace.member.update"
			var request service.WorkspaceMemberUpdate
			if err = common.DecodeJson(c.Request.Body, &request); err == nil {
				err = service.UpdateWorkspaceMember(team.OwnerUserID, memberID, request, management)
			}
		}
	}
	recordUserSecurityAudit(c, c.GetInt("id"), action, map[string]any{"success": err == nil, "team_id": teamID, "target_user_id": memberID})
	workspaceResult(c, nil, err, "", 0)
}

func RenameWorkspaceTeam(c *gin.Context) {
	var request struct {
		TeamID int    `json:"team_id"`
		Name   string `json:"name"`
	}
	err := common.DecodeJson(c.Request.Body, &request)
	if err == nil {
		err = model.RenameWorkspaceTeam(c.GetInt("id"), request.TeamID, request.Name)
	}
	recordUserSecurityAudit(c, c.GetInt("id"), "workspace.team.rename", map[string]any{"success": err == nil, "team_id": request.TeamID})
	workspaceResult(c, nil, err, "", 0)
}

func GetAdminWorkspaceTeamTrends(c *gin.Context) {
	teamID, err := strconv.Atoi(c.Param("team_id"))
	if err != nil || teamID <= 0 {
		workspaceResult(c, nil, model.ErrWorkspaceAccess, "", 0)
		return
	}
	start, end, ok := parseFlowQuotaTimeRange(c)
	if !ok {
		return
	}
	if end-start > 30*24*3600 {
		common.ApiErrorMsg(c, "请选择不超过 30 天的有效时间范围")
		return
	}
	var team model.WorkspaceTeam
	if err = model.DB.First(&team, teamID).Error; err != nil {
		workspaceResult(c, nil, model.ErrWorkspaceAccess, "", 0)
		return
	}
	data, err := model.GetWorkspaceTeamTrends(team.OwnerUserID, start, end, teamID)
	workspaceResult(c, data, err, "", 0)
}
