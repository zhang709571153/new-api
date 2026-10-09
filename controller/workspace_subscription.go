package controller

import (
	"errors"
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/shopspring/decimal"
	"strconv"
)

func AdminSetWorkspaceSubscription(c *gin.Context) {
	userID, err := strconv.Atoi(c.Param("id"))
	if err != nil || userID <= 0 {
		common.ApiError(c, errors.New("用户无效"))
		return
	}
	target, err := model.GetUserById(userID, false)
	if err != nil || !canManageTargetRole(c.GetInt("role"), target.Role) {
		common.ApiError(c, errors.New("无权修改该用户的额度"))
		return
	}
	var request struct {
		FundingContext  *model.WorkspaceFundingContext `json:"funding_context"`
		SubscriptionID  *int                           `json:"subscription_id"`
		TeamID          *int                           `json:"team_id"`
		MonthlyUSD      *decimal.Decimal               `json:"monthly_usd"`
		WeeklyUSD       *decimal.Decimal               `json:"weekly_usd"`
		WeeklyUsedUSD   *decimal.Decimal               `json:"weekly_used_usd"`
		WeeklyResetAt   *int64                         `json:"weekly_reset_at"`
		EndTime         *int64                         `json:"end_time"`
		ExpectedEndTime *int64                         `json:"expected_end_time"`
	}
	if err := common.DecodeJson(c.Request.Body, &request); err != nil {
		common.ApiError(c, errors.New("额度参数无效"))
		return
	}
	teamID := 0
	if request.SubscriptionID != nil && (request.TeamID == nil || *request.SubscriptionID <= 0) {
		common.ApiErrorMsg(c, "请明确选择订阅及个人或团队额度后重试")
		return
	}
	if request.TeamID != nil {
		teamID = *request.TeamID
	} else {
		_, team, err := model.FindWorkspaceMembership(userID)
		if err != nil {
			common.ApiError(c, err)
			return
		}
		if team != nil && team.OwnerUserID == userID && team.FundingVersion > 0 {
			common.ApiErrorMsg(c, "请明确选择个人或团队额度后重试")
			return
		}
	}
	if request.EndTime != nil || request.ExpectedEndTime != nil {
		if request.EndTime == nil || request.ExpectedEndTime == nil || request.SubscriptionID == nil || request.TeamID == nil || request.WeeklyUSD != nil || request.MonthlyUSD != nil || request.WeeklyUsedUSD != nil || request.WeeklyResetAt != nil {
			common.ApiErrorMsg(c, "请单独提交所选订阅的新到期时间及原到期时间")
			return
		}
		result, err := model.UpdateWorkspaceSubscriptionExpiry(userID, teamID, *request.SubscriptionID, *request.EndTime, *request.ExpectedEndTime, request.FundingContext)
		if err != nil {
			common.ApiError(c, err)
			return
		}
		recordManageAuditFor(c, userID, "subscription.expiry_update", map[string]any{"subscription_id": result.Id, "team_id": teamID, "previous_end_time": *request.ExpectedEndTime, "end_time": result.EndTime})
		c.Header("Cache-Control", "no-store")
		common.ApiSuccess(c, result)
		return
	}
	if request.WeeklyUsedUSD != nil {
		if c.GetInt("role") < common.RoleRootUser || teamID <= 0 || request.SubscriptionID == nil || request.WeeklyUSD != nil || request.MonthlyUSD != nil {
			common.ApiErrorMsg(c, "仅超级管理员可单独修改团队本周已用额度")
			return
		}
		if request.WeeklyResetAt == nil || *request.WeeklyResetAt <= 0 {
			common.ApiErrorMsg(c, "周额度周期已变化，请刷新页面后重试")
			return
		}
		used, err := common.WalletQuotaFromDecimalStrict(request.WeeklyUsedUSD.Mul(decimal.NewFromFloat(common.QuotaPerUnit)))
		if err != nil || request.WeeklyUsedUSD.IsNegative() {
			common.ApiErrorMsg(c, "本周已用额度超出范围")
			return
		}
		result, err := model.UpdateWorkspaceTeamSubscriptionWeeklyUsed(userID, teamID, *request.SubscriptionID, int64(used), *request.WeeklyResetAt, request.FundingContext)
		if err != nil {
			common.ApiError(c, err)
			return
		}
		recordManageAuditFor(c, userID, "subscription.weekly_usage_update", map[string]any{"subscription_id": result.Id, "team_id": teamID, "weekly_used": used, "weekly_reset_at": result.WeeklyResetAt})
		c.Header("Cache-Control", "no-store")
		common.ApiSuccess(c, result)
		return
	}
	if request.WeeklyUSD == nil {
		common.ApiErrorMsg(c, "请填写周限额")
		return
	}
	var monthly int
	if request.MonthlyUSD != nil {
		monthly, err = common.WalletQuotaFromDecimalStrict(request.MonthlyUSD.Mul(decimal.NewFromFloat(common.QuotaPerUnit)))
		if err != nil {
			common.ApiError(c, errors.New("月额度超出范围"))
			return
		}
	}
	weekly, err := common.WalletQuotaFromDecimalStrict(request.WeeklyUSD.Mul(decimal.NewFromFloat(common.QuotaPerUnit)))
	if err != nil {
		common.ApiError(c, errors.New("周限额超出范围"))
		return
	}
	var result *model.UserSubscription
	if teamID < 0 {
		common.ApiError(c, model.ErrWorkspaceAccess)
		return
	}
	if request.MonthlyUSD == nil && request.SubscriptionID != nil {
		result, err = model.UpdateWorkspaceSubscriptionWeeklyLimit(userID, teamID, *request.SubscriptionID, int64(weekly), request.FundingContext)
	} else if request.MonthlyUSD == nil {
		result, err = model.SetWorkspaceWeeklySubscription(userID, teamID, int64(weekly), request.FundingContext)
	} else if request.SubscriptionID != nil {
		result, err = model.UpdateWorkspaceSubscriptionLimits(userID, teamID, *request.SubscriptionID, int64(monthly), int64(weekly), request.FundingContext)
	} else if teamID > 0 {
		result, err = model.SetWorkspaceTeamSubscription(userID, teamID, int64(monthly), int64(weekly), request.FundingContext)
	} else {
		result, err = model.SetWorkspaceSubscription(userID, int64(monthly), int64(weekly), request.FundingContext)
	}
	if err != nil {
		common.ApiError(c, err)
		return
	}
	recordManageAuditFor(c, userID, "subscription.allowance_update", map[string]any{"subscription_id": result.Id, "team_id": result.WorkspaceTeamID, "monthly_quota": result.AmountTotal, "weekly_quota": weekly})
	c.Header("Cache-Control", "no-store")
	common.ApiSuccess(c, result)
}
