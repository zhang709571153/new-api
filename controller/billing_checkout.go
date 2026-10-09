package controller

import (
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/Calcium-Ion/go-epay/epay"
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/shopspring/decimal"
	"github.com/tidwall/gjson"
)

func BillingCatalog(c *gin.Context) {
	var plans []model.SubscriptionPlan
	if err := model.DB.Where("currency = ? AND enabled = ?", "CNY", true).Order("sort_order desc, price_amount asc, id asc").Find(&plans).Error; err != nil {
		common.ApiError(c, err)
		return
	}
	personalPlans, teamPlans := []model.SubscriptionPlan{}, []model.SubscriptionPlan{}
	for _, plan := range plans {
		plan.FundingScope = model.NormalizeFundingScope(plan.FundingScope)
		if plan.FundingScope == "team" {
			teamPlans = append(teamPlans, plan)
		} else if plan.FundingScope == "personal" {
			personalPlans = append(personalPlans, plan)
		}
	}
	var owned model.WorkspaceTeam
	if err := model.DB.Where("owner_user_id = ?", c.GetInt("id")).Limit(1).Find(&owned).Error; err != nil {
		common.ApiError(c, err)
		return
	}
	var personal, team []model.UserSubscription
	now := model.GetDBTimestamp()
	if err := model.DB.Where("user_id = ? AND workspace_team_id = 0 AND status = ? AND end_time > ?", c.GetInt("id"), "active", now).Find(&personal).Error; err != nil {
		common.ApiError(c, err)
		return
	}
	if owned.ID > 0 {
		if err := model.DB.Where("workspace_team_id = ? AND status = ? AND end_time > ?", owned.ID, "active", now).Find(&team).Error; err != nil {
			common.ApiError(c, err)
			return
		}
	}
	var current, currentTeam *model.UserSubscription
	if len(personal) == 1 {
		personal[0].RefreshWeeklyWindow(now)
		current = &personal[0]
	}
	if len(team) == 1 {
		team[0].RefreshWeeklyWindow(now)
		currentTeam = &team[0]
	}
	common.ApiSuccess(c, gin.H{"plans": personalPlans, "team_plans": teamPlans, "current_subscription": current, "current_team_subscription": currentTeam, "owned_team_id": owned.ID, "subscription_conflict": len(personal) > 1, "team_subscription_conflict": len(team) > 1, "storefront_enabled": operation_setting.GetPaymentSetting().StorefrontEnabled, "cny_per_usd": model.BillingCNYPerUSD, "paygo_min": 0.01, "paygo_step": 0.01, "paygo_max": 10000})
}

func requireBillingStorefront(c *gin.Context) bool {
	if !operation_setting.GetPaymentSetting().StorefrontEnabled {
		common.ApiErrorMsg(c, "缺货")
		return false
	}
	return requirePaymentCompliance(c)
}

func AdminInitializeBillingPlans(c *gin.Context) {
	if err := model.InitializeCNYPlans(); err != nil {
		common.ApiError(c, err)
		return
	}
	model.RecordLog(c.GetInt("id"), model.LogTypeSystem, "Initialized CNY subscription catalog")
	common.ApiSuccess(c, nil)
}

func BillingCreateCheckout(c *gin.Context) {
	if !requireBillingStorefront(c) {
		return
	}
	var req model.BillingCheckoutRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		common.ApiErrorMsg(c, "参数错误")
		return
	}
	order, err := model.CreateBillingCheckout(c.GetInt("id"), req)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	common.ApiSuccess(c, order)
}

func BillingOrders(c *gin.Context) {
	orders, err := model.ListBillingOrders(c.GetInt("id"))
	if err != nil {
		common.ApiError(c, err)
		return
	}
	common.ApiSuccess(c, orders)
}

func BillingOrderSelf(c *gin.Context) {
	order, err := model.GetBillingOrder(c.GetInt("id"), c.Param("trade_no"))
	if err != nil {
		common.ApiErrorMsg(c, "订单不存在")
		return
	}
	common.ApiSuccess(c, order)
}

func BillingBalancePay(c *gin.Context) {
	if !requireBillingStorefront(c) {
		return
	}
	order, err := model.FulfillBillingOrder(c.GetInt("id"), c.Param("trade_no"), "balance", 0, false)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	common.ApiSuccess(c, order)
}

func BillingEpayPay(c *gin.Context) {
	if !requireBillingStorefront(c) {
		return
	}
	var req struct {
		PaymentMethod string `json:"payment_method"`
	}
	if err := c.ShouldBindJSON(&req); err != nil || !operation_setting.ContainsPayMethod(req.PaymentMethod) || (req.PaymentMethod != "alipay" && req.PaymentMethod != "wxpay") {
		common.ApiErrorMsg(c, "支付方式不可用")
		return
	}
	client := GetEpayClient()
	if client == nil {
		common.ApiErrorMsg(c, "管理员尚未配置支付网关")
		return
	}
	order, err := model.ClaimBillingEpay(c.GetInt("id"), c.Param("trade_no"), "epay:"+req.PaymentMethod)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	base := strings.TrimRight(service.GetCallbackAddress(), "/")
	notify, err := url.Parse(base + "/api/billing/epay/notify")
	if err != nil {
		common.ApiErrorMsg(c, "支付回调配置错误")
		return
	}
	returnURL, _ := url.Parse(base + "/api/billing/epay/return")
	name := "Realyu API 充值"
	if order.PlanTitle != "" {
		name = "Realyu API " + order.PlanTitle
	}
	uri, params, err := client.Purchase(&epay.PurchaseArgs{Type: req.PaymentMethod, ServiceTradeNo: order.TradeNo, Name: name, Money: decimal.NewFromInt(order.PriceCents).Div(decimal.NewFromInt(100)).StringFixed(2), Device: epay.PC, NotifyUrl: notify, ReturnUrl: returnURL})
	if err != nil {
		common.ApiErrorMsg(c, "支付请求失败，请重试")
		return
	}
	common.ApiSuccess(c, gin.H{"url": uri, "params": params})
}

func billingCallbackParams(c *gin.Context) (map[string]string, error) {
	if err := c.Request.ParseForm(); err != nil {
		return nil, err
	}
	params := make(map[string]string)
	for key, values := range c.Request.Form {
		if len(values) != 1 {
			return nil, errors.New("duplicate callback parameter")
		}
		params[key] = values[0]
	}
	return params, nil
}

func billingVerifyCallback(c *gin.Context) (*model.BillingOrder, error) {
	params, err := billingCallbackParams(c)
	if err != nil {
		return nil, err
	}
	client := GetEpayClient()
	if client == nil {
		return nil, errors.New("payment unavailable")
	}
	verified, err := client.Verify(params)
	if err != nil || !verified.VerifyStatus || params["pid"] != operation_setting.EpayId || verified.TradeStatus != epay.StatusTradeSuccess {
		return nil, errors.New("invalid payment callback")
	}
	order, err := model.GetBillingOrderForPayment(verified.ServiceTradeNo)
	if err != nil {
		return nil, err
	}
	amount, err := decimal.NewFromString(verified.Money)
	if err != nil || !amount.Mul(decimal.NewFromInt(100)).Equal(decimal.NewFromInt(order.PriceCents)) || order.PaymentMethod != "epay:"+billingGatewayPaymentMethod(verified.Type) {
		return nil, errors.New("payment details mismatch")
	}
	return order, nil
}

var billingGatewayHTTPClient = &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}

// Z-Pay's verified production order query uses alipay9 for the channel behind
// an alipay checkout. Only this observed alias on the configured Z-Pay host is
// equivalent; arbitrary suffixes and other providers must still match exactly.
func billingGatewayPaymentMethod(method string) string {
	if method == "alipay9" {
		gateway, err := url.Parse(operation_setting.PayAddress)
		if err == nil && gateway.Scheme == "https" && strings.EqualFold(gateway.Hostname(), "zpayz.cn") {
			return "alipay"
		}
	}
	return method
}

// Query credentials stay on the server, are never logged or returned. Query
// verification is shared by callbacks and the user's explicit reconciliation.
func billingQueryGateway(order *model.BillingOrder) error {
	base, err := url.Parse(operation_setting.PayAddress)
	if err != nil || base.Scheme != "https" || base.Host == "" || base.User != nil {
		return errors.New("payment gateway requires HTTPS")
	}
	base.Path = strings.TrimRight(base.Path, "/") + "/api.php"
	base.RawQuery = url.Values{"act": {"order"}, "pid": {operation_setting.EpayId}, "key": {operation_setting.EpayKey}, "out_trade_no": {order.TradeNo}}.Encode()
	response, err := billingGatewayHTTPClient.Get(base.String())
	if err != nil {
		return errors.New("payment verification unavailable; please retry")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 32769))
	if err != nil || response.StatusCode != http.StatusOK || len(data) > 32768 || !gjson.ValidBytes(data) {
		return errors.New("invalid payment verification response")
	}
	value := gjson.ParseBytes(data)
	amount, err := decimal.NewFromString(value.Get("money").String())
	if err != nil || value.Get("code").Int() != 1 || value.Get("status").Int() != 1 || value.Get("pid").String() != operation_setting.EpayId || value.Get("out_trade_no").String() != order.TradeNo || "epay:"+billingGatewayPaymentMethod(value.Get("type").String()) != order.PaymentMethod || !amount.Mul(decimal.NewFromInt(100)).Equal(decimal.NewFromInt(order.PriceCents)) {
		return errors.New("payment not confirmed")
	}
	return nil
}

func billingReconcile(order *model.BillingOrder) (*model.BillingOrder, error) {
	if !model.IsBillingEpayMethod(order.PaymentMethod) {
		return nil, errors.New("not an online payment")
	}
	if order.Status == "paid" || order.Status == "wallet_credited" {
		return order, nil
	}
	if err := billingQueryGateway(order); err != nil {
		return nil, err
	}
	return model.FulfillBillingOrder(order.UserId, order.TradeNo, order.PaymentMethod, order.PriceCents, true)
}

func BillingReconcile(c *gin.Context) {
	order, err := model.GetBillingOrder(c.GetInt("id"), c.Param("trade_no"))
	if err == nil {
		order, err = billingReconcile(order)
	}
	if err != nil {
		common.ApiErrorMsg(c, "付款尚未确认，请稍后重试")
		return
	}
	common.ApiSuccess(c, order)
}

func BillingEpayNotify(c *gin.Context) {
	order, err := billingVerifyCallback(c)
	if err == nil {
		_, err = billingReconcile(order)
	}
	if err != nil {
		c.String(http.StatusOK, "fail")
		return
	}
	c.String(http.StatusOK, "success")
}

// Browser return is navigation only. It never grants wallet or plan rights.
func BillingEpayReturn(c *gin.Context) { c.Redirect(http.StatusSeeOther, "/wallet") }
