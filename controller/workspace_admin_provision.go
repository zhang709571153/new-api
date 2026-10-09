package controller

import (
	"errors"
	"strconv"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/shopspring/decimal"
)

func AdminProvisionWorkspaceTeam(c *gin.Context) {
	userID, err := strconv.Atoi(c.Param("id"))
	if err != nil || userID <= 0 {
		common.ApiErrorMsg(c, "用户无效")
		return
	}
	target, err := model.GetUserById(userID, false)
	if err != nil || !canManageTargetRole(c.GetInt("role"), target.Role) {
		common.ApiErrorMsg(c, "无权为该用户开通团队订阅")
		return
	}
	var request struct {
		Name      string          `json:"name"`
		WeeklyUSD decimal.Decimal `json:"weekly_usd"`
	}
	if err := common.DecodeJson(c.Request.Body, &request); err != nil {
		common.ApiErrorMsg(c, "团队参数无效")
		return
	}
	weekly, err := common.WalletQuotaFromDecimalStrict(request.WeeklyUSD.Mul(decimal.NewFromFloat(common.QuotaPerUnit)))
	if err != nil || weekly <= 0 {
		common.ApiError(c, errors.New("周额度必须大于零且在支持范围内"))
		return
	}
	result, err := model.AdminProvisionWorkspaceTeam(userID, request.Name, int64(weekly))
	if err != nil {
		common.ApiError(c, err)
		return
	}
	recordManageAuditFor(c, userID, "subscription.team_provision", map[string]any{"team_id": result.Team.ID, "subscription_id": result.Subscription.Id, "weekly_quota": weekly})
	c.Header("Cache-Control", "no-store")
	common.ApiSuccess(c, result)
}
