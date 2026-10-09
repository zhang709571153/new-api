package controller

import (
	"errors"
	"strconv"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

func GetTeamOverview(c *gin.Context) {
	end := time.Now().Unix() + 1
	start := end - 86400
	for name, target := range map[string]*int64{"start_timestamp": &start, "end_timestamp": &end} {
		if value := c.Query(name); value != "" {
			parsed, err := strconv.ParseInt(value, 10, 64)
			if err != nil || parsed < 0 {
				common.ApiError(c, errors.New("时间范围无效"))
				return
			}
			*target = parsed
		}
	}
	if end <= start || end-start > 366*86400 {
		common.ApiError(c, errors.New("请选择一年以内的有效时间范围"))
		return
	}
	result, err := model.GetTeamOverview(start, end)
	if err != nil {
		common.SysError("team overview query failed: " + err.Error())
		common.ApiError(c, errors.New("读取团队用量失败"))
		return
	}
	c.Header("Cache-Control", "no-store")
	common.ApiSuccess(c, result)
}

func CreateTeamMember(c *gin.Context) {
	var req service.CreateTeamMemberRequest
	if err := common.DecodeJson(c.Request.Body, &req); err != nil {
		common.ApiError(c, errors.New("开户参数无效"))
		return
	}
	result, err := service.ProvisionTeamMember(req)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	recordManageAuditFor(c, result.User.Id, "user.create", map[string]any{
		"username": result.User.Username, "role": common.RoleCommonUser,
		"quota": result.User.Quota, "token_id": result.TokenId, "api_only": true,
	})
	c.Header("Cache-Control", "no-store")
	common.ApiSuccess(c, result)
}
