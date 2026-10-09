package controller

import (
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	"github.com/Calcium-Ion/go-epay/epay"
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
)

type billingGatewayTestTransport func(*http.Request) (*http.Response, error)

func (transport billingGatewayTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return transport(request)
}

func TestBillingZpayReconciliation(t *testing.T) {
	buyer, _ := billingFixture(t)
	for _, test := range []struct {
		name, host, method, field, value string
		accepted                         bool
	}{
		{name: "standard alipay", host: "zpayz.cn", method: "alipay", accepted: true},
		{name: "observed ZPay alipay9 channel", host: "zpayz.cn", method: "alipay9", accepted: true},
		{name: "alias not allowed on other gateways", host: "other.example", method: "alipay9"},
		{name: "lookalike host rejected", host: "zpayz.cn.example", method: "alipay9"},
		{name: "unknown alipay suffix rejected", host: "zpayz.cn", method: "alipay999"},
		{name: "wrong method rejected", host: "zpayz.cn", method: "wxpay"},
		{name: "wrong amount rejected", host: "zpayz.cn", method: "alipay9", field: "money", value: "2.99"},
		{name: "wrong merchant rejected", host: "zpayz.cn", method: "alipay9", field: "pid", value: "10002"},
		{name: "wrong order rejected", host: "zpayz.cn", method: "alipay9", field: "out_trade_no", value: "other-order"},
		{name: "unpaid rejected", host: "zpayz.cn", method: "alipay9", field: "status", value: "0"},
	} {
		t.Run(test.name, func(t *testing.T) {
			var user model.User
			require.NoError(t, model.DB.First(&user, buyer.Id).Error)
			oldURL, oldID, oldKey, oldClient := operation_setting.PayAddress, operation_setting.EpayId, operation_setting.EpayKey, billingGatewayHTTPClient
			t.Cleanup(func() {
				operation_setting.PayAddress, operation_setting.EpayId, operation_setting.EpayKey, billingGatewayHTTPClient = oldURL, oldID, oldKey, oldClient
			})
			operation_setting.PayAddress = "https://" + test.host
			operation_setting.EpayId, operation_setting.EpayKey = "10001", "test-only-key"
			order, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "paygo", AmountCNY: 3, IdempotencyKey: "zpay-reconcile-" + test.name})
			require.NoError(t, err)
			_, err = model.ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
			require.NoError(t, err)
			// Paid orders remain recoverable after the original checkout expires.
			require.NoError(t, model.DB.Model(order).Update("expires_at", model.GetDBTimestamp()-1).Error)
			query := map[string]any{"code": 1, "status": 1, "pid": "10001", "out_trade_no": order.TradeNo, "trade_no": "zpay-transaction", "type": test.method, "money": "3.00"}
			if test.field != "" {
				query[test.field] = test.value
			}
			body, err := common.Marshal(query)
			require.NoError(t, err)
			billingGatewayHTTPClient = &http.Client{Transport: billingGatewayTestTransport(func(req *http.Request) (*http.Response, error) {
				require.Equal(t, "https", req.URL.Scheme)
				require.Equal(t, test.host, req.URL.Hostname())
				require.Equal(t, order.TradeNo, req.URL.Query().Get("out_trade_no"))
				return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(string(body)))}, nil
			})}
			router := gin.New()
			router.Use(func(c *gin.Context) { c.Set("id", user.Id) })
			router.POST("/orders/:trade_no/reconcile", BillingReconcile)
			router.GET("/notify", BillingEpayNotify)
			params := epay.GenerateParams(map[string]string{"pid": "10001", "out_trade_no": order.TradeNo, "trade_no": "zpay-transaction", "money": "3.00", "type": "alipay", "trade_status": "TRADE_SUCCESS"}, operation_setting.EpayKey)
			values := url.Values{}
			for key, value := range params {
				values.Set(key, value)
			}
			// Mix delayed callbacks and explicit reconciliation concurrently.
			var wg sync.WaitGroup
			responses := make(chan bool, 8)
			for i := range 8 {
				wg.Go(func() {
					response := httptest.NewRecorder()
					if i%2 == 0 {
						router.ServeHTTP(response, httptest.NewRequest("GET", "/notify?"+values.Encode(), nil))
						responses <- response.Body.String() == "success"
					} else {
						router.ServeHTTP(response, httptest.NewRequest("POST", "/orders/"+order.TradeNo+"/reconcile", nil))
						responses <- gjson.Get(response.Body.String(), "success").Bool()
					}
				})
			}
			wg.Wait()
			close(responses)
			for accepted := range responses {
				require.Equal(t, test.accepted, accepted)
			}
			var after model.User
			require.NoError(t, model.DB.First(&after, user.Id).Error)
			saved, err := model.GetBillingOrder(user.Id, order.TradeNo)
			require.NoError(t, err)
			if test.accepted {
				require.Equal(t, "wallet_credited", saved.Status)
				require.Equal(t, user.Quota+int(order.CreditedQuota), after.Quota, "one payment must credit exactly once")
			} else {
				require.Equal(t, "pending", saved.Status)
				require.Equal(t, user.Quota, after.Quota)
			}
		})
	}
}

func TestBillingStorefrontSwitch(t *testing.T) {
	user, plans := billingFixture(t)
	settings := operation_setting.GetPaymentSetting()
	previous := *settings
	t.Cleanup(func() { *settings = previous })
	settings.ComplianceConfirmed = true
	settings.ComplianceTermsVersion = operation_setting.CurrentComplianceTermsVersion
	settings.StorefrontEnabled = false
	router := gin.New()
	router.Use(func(c *gin.Context) { c.Set("id", user.Id) })
	router.GET("/catalog", BillingCatalog)
	router.POST("/orders", BillingCreateCheckout)
	router.POST("/orders/:trade_no/balance", BillingBalancePay)
	router.POST("/orders/:trade_no/epay", BillingEpayPay)
	for _, path := range []string{"/orders", "/orders/pending/balance", "/orders/pending/epay"} {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest("POST", path, strings.NewReader(`{}`)))
		require.False(t, gjson.Get(response.Body.String(), "success").Bool())
		require.Equal(t, "缺货", gjson.Get(response.Body.String(), "message").String())
	}
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest("GET", "/catalog", nil))
	require.False(t, gjson.Get(response.Body.String(), "data.storefront_enabled").Bool())
	require.Len(t, gjson.Get(response.Body.String(), "data.plans").Array(), 6)
	require.Equal(t, 0.01, gjson.Get(response.Body.String(), "data.paygo_min").Float())
	require.Equal(t, 0.01, gjson.Get(response.Body.String(), "data.paygo_step").Float())
	settings.StorefrontEnabled = true
	for _, amount := range []string{"0.01", "1.23", "49", "51", "10000"} {
		payload := fmt.Sprintf(`{"kind":"paygo","amount_cny":%s,"idempotency_key":"custom-amount-%s-key"}`, amount, amount)
		request := httptest.NewRequest("POST", "/orders", strings.NewReader(payload))
		request.Header.Set("Content-Type", "application/json")
		response = httptest.NewRecorder()
		router.ServeHTTP(response, request)
		require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
	}
	request := httptest.NewRequest("POST", "/orders", strings.NewReader(fmt.Sprintf(`{"kind":"subscription","plan_id":%d,"idempotency_key":"storefront-open-purchase"}`, plans[0].Id)))
	request.Header.Set("Content-Type", "application/json")
	response = httptest.NewRecorder()
	router.ServeHTTP(response, request)
	require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
	var after model.User
	require.NoError(t, model.DB.First(&after, user.Id).Error)
	require.Equal(t, user.Quota, after.Quota, "a quote and closed storefront must not debit balance")
}

func TestBillingResidualConversionAndStaleQuote(t *testing.T) {
	user, plans := billingFixture(t)
	initial, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[2].Id, IdempotencyKey: "conversion-initial-purchase"})
	require.NoError(t, err)
	initial, err = model.FulfillBillingOrder(user.Id, initial.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	var old model.UserSubscription
	require.NoError(t, model.DB.First(&old, initial.SubscriptionId).Error)
	// Reserve real usage before changing plans. Its later settlement must stay
	// attached to the old subscription and cannot refill the new allowance.
	_, err = model.PreConsumeUserSubscription("conversion-in-flight", user.Id, "gpt-6-sol", 0, 50)
	require.NoError(t, err)
	conversion, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "convert", PlanId: plans[3].Id, OldSubscriptionId: old.Id, IdempotencyKey: "conversion-only-checkout"})
	require.NoError(t, err)
	require.Zero(t, conversion.PriceCents)
	var before model.User
	require.NoError(t, model.DB.First(&before, user.Id).Error)
	converted, err := model.FulfillBillingOrder(user.Id, conversion.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	var after model.User
	require.NoError(t, model.DB.First(&after, user.Id).Error)
	require.Equal(t, before.Quota, after.Quota)
	require.NoError(t, model.PostConsumeUserSubscriptionDelta(old.Id, 20, "conversion-in-flight"))
	require.NoError(t, model.RefundSubscriptionPreConsume("conversion-in-flight"))
	var current model.UserSubscription
	require.NoError(t, model.DB.First(&current, converted.SubscriptionId).Error)
	require.Zero(t, current.AmountUsed)
	require.Equal(t, conversion.ExtensionSeconds, current.EndTime-current.StartTime)
	// A quote based on the first week becomes stale at the next reset.
	// Use a fresh paid 28-day upgrade (no complete future week remains above).
	full, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "upgrade", PlanId: plans[4].Id, OldSubscriptionId: current.Id, IdempotencyKey: "conversion-buy-full-period"})
	require.NoError(t, err)
	full, err = model.FulfillBillingOrder(user.Id, full.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	current = model.UserSubscription{}
	require.NoError(t, model.DB.First(&current, full.SubscriptionId).Error)
	stale, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "upgrade", PlanId: plans[5].Id, OldSubscriptionId: current.Id, IdempotencyKey: "stale-week-upgrade-checkout"})
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&current).Updates(map[string]any{"start_time": current.StartTime - model.BillingWeekSeconds, "end_time": current.EndTime - model.BillingWeekSeconds}).Error)
	_, err = model.FulfillBillingOrder(user.Id, stale.TradeNo, "balance", 0, false)
	require.Error(t, err)
	_, err = model.ClaimBillingEpay(user.Id, stale.TradeNo, "epay:alipay")
	require.NoError(t, err)
	settled, err := model.FulfillBillingOrder(user.Id, stale.TradeNo, "epay:alipay", stale.PriceCents, true)
	require.NoError(t, err)
	require.Equal(t, "wallet_credited", settled.Status)
	require.NoError(t, model.DB.First(&current, current.Id).Error)
	require.Equal(t, "active", current.Status)
}

func TestBillingAdminPlanValidation(t *testing.T) {
	_, plans := billingFixture(t)
	payment := operation_setting.GetPaymentSetting()
	saved := *payment
	t.Cleanup(func() { *payment = saved })
	payment.ComplianceConfirmed = false
	payment.ComplianceTermsVersion = operation_setting.CurrentComplianceTermsVersion
	router := gin.New()
	router.POST("/plans", AdminCreateSubscriptionPlan)
	router.PUT("/plans/:id", AdminUpdateSubscriptionPlan)
	plan := plans[0]
	plan.Id = 0
	plan.Title = "Disabled custom plan"
	plan.Enabled = false
	call := func(method, path string, p model.SubscriptionPlan) *httptest.ResponseRecorder {
		body, err := common.Marshal(AdminUpsertSubscriptionPlanRequest{Plan: p})
		require.NoError(t, err)
		r := httptest.NewRequest(method, path, strings.NewReader(string(body)))
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		return w
	}
	created := call("POST", "/plans", plan)
	require.True(t, gjson.Get(created.Body.String(), "success").Bool(), created.Body.String())
	var stored model.SubscriptionPlan
	require.NoError(t, model.DB.Where("title = ?", plan.Title).First(&stored).Error)
	require.False(t, stored.Enabled, "GORM's legacy default:true must not publish a disabled draft")
	stored.PriceAmount = 149
	stored.WeeklyAmount *= 2
	stored.TotalAmount = stored.WeeklyAmount * 4
	updated := call("PUT", fmt.Sprintf("/plans/%d", stored.Id), stored)
	require.True(t, gjson.Get(updated.Body.String(), "success").Bool(), updated.Body.String())
	stored.TotalAmount++
	invalid := call("PUT", fmt.Sprintf("/plans/%d", stored.Id), stored)
	require.False(t, gjson.Get(invalid.Body.String(), "success").Bool())
	var unchanged model.SubscriptionPlan
	require.NoError(t, model.DB.First(&unchanged, stored.Id).Error)
	require.Equal(t, unchanged.WeeklyAmount*4, unchanged.TotalAmount)
}

func TestBillingPaidCNYPreferenceAlwaysUsesSubscriptionFirst(t *testing.T) {
	user, plans := billingFixture(t)
	router := gin.New()
	router.Use(func(c *gin.Context) { c.Set("id", user.Id) })
	router.PUT("/preference", UpdateSubscriptionPreference)
	router.GET("/self", GetSubscriptionSelf)
	setPreference := func(pref string) string {
		body, err := common.Marshal(BillingPreferenceRequest{BillingPreference: pref})
		require.NoError(t, err)
		request := httptest.NewRequest("PUT", "/preference", strings.NewReader(string(body)))
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
		return gjson.Get(response.Body.String(), "data.billing_preference").String()
	}
	// Accounts without a purchased CNY subscription retain the legacy choice.
	require.Equal(t, "wallet_first", setPreference("wallet_first"))
	order, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[0].Id, IdempotencyKey: "preference-purchase"})
	require.NoError(t, err)
	order, err = model.FulfillBillingOrder(user.Id, order.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	for _, pref := range []string{"wallet_first", "wallet_only", "subscription_only", "subscription_first"} {
		require.Equal(t, "subscription_first", setPreference(pref))
		var stored model.User
		require.NoError(t, model.DB.First(&stored, user.Id).Error)
		require.Equal(t, "subscription_first", stored.GetSetting().BillingPreference)
	}
	// Older clients may already have persisted a conflicting preference. The
	// self endpoint must report the policy that the relay actually applies.
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", user.Id).Update("setting", `{"billing_preference":"wallet_only"}`).Error)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest("GET", "/self", nil))
	require.True(t, gjson.Get(response.Body.String(), "success").Bool(), response.Body.String())
	require.Equal(t, "subscription_first", gjson.Get(response.Body.String(), "data.billing_preference").String())
	require.NoError(t, model.DB.Model(&model.UserSubscription{}).Where("id = ?", order.SubscriptionId).Update("status", "expired").Error)
	require.Equal(t, "wallet_first", setPreference("wallet_first"))
}

func TestBillingPaidSubscriptionDeleteGuard(t *testing.T) {
	user, plans := billingFixture(t)
	order, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[0].Id, IdempotencyKey: "delete-guard-purchase"})
	require.NoError(t, err)
	order, err = model.FulfillBillingOrder(user.Id, order.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	var before model.User
	require.NoError(t, model.DB.First(&before, user.Id).Error)
	reserved, err := model.UpdateSubscriptionFirstFunding(user.Id, "delete-guard-inflight", plans[0].WeeklyAmount+50, "reserve")
	require.NoError(t, err)
	require.EqualValues(t, 50, reserved.Record.WalletPreConsumed)
	router := gin.New()
	// This test checks ledger retention after authorization. Role denials use
	// real sessions in TestWorkspaceFundingSubscriptionManagementRoles.
	router.Use(func(c *gin.Context) { c.Set("role", common.RoleRootUser); c.Next() })
	router.DELETE("/subscriptions/:id", AdminDeleteUserSubscription)
	router.POST("/subscriptions/:id/invalidate", AdminInvalidateUserSubscription)
	call := func(method, suffix string) *httptest.ResponseRecorder {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(method, fmt.Sprintf("/subscriptions/%d%s", order.SubscriptionId, suffix), nil))
		return response
	}
	deleted := call("DELETE", "")
	require.False(t, gjson.Get(deleted.Body.String(), "success").Bool(), "paid subscriptions must retain the ledger row")
	require.Contains(t, gjson.Get(deleted.Body.String(), "message").String(), "取消")
	var stored model.UserSubscription
	require.NoError(t, model.DB.First(&stored, order.SubscriptionId).Error)
	require.Equal(t, "active", stored.Status)
	cancelled := call("POST", "/invalidate")
	require.True(t, gjson.Get(cancelled.Body.String(), "success").Bool(), cancelled.Body.String())
	require.NoError(t, model.DB.First(&stored, order.SubscriptionId).Error)
	require.Equal(t, "cancelled", stored.Status)
	deleted = call("DELETE", "")
	require.False(t, gjson.Get(deleted.Body.String(), "success").Bool(), "cancellation must not allow deleting refund dependencies")
	refunded, err := model.UpdateSubscriptionFirstFunding(user.Id, "delete-guard-inflight", 0, "refund")
	require.NoError(t, err)
	require.Equal(t, "refunded", refunded.Record.Status)
	var after model.User
	require.NoError(t, model.DB.First(&after, user.Id).Error)
	require.Equal(t, before.Quota, after.Quota, "the PAYGO part of an in-flight request remains refundable after cancellation")
	require.NoError(t, model.DB.First(&stored, order.SubscriptionId).Error)
	require.Zero(t, stored.AmountUsed)

	legacy := model.UserSubscription{UserId: user.Id, Status: "cancelled", Source: "admin", StartTime: common.GetTimestamp(), EndTime: common.GetTimestamp()}
	require.NoError(t, model.DB.Create(&legacy).Error)
	_, err = model.AdminDeleteUserSubscription(legacy.Id)
	require.NoError(t, err, "legacy administration behavior is unchanged")
}

func TestBillingCatalogInitialization(t *testing.T) {
	setupTeamDatabase(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Option{}))
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		connection, err := model.DB.DB()
		require.NoError(t, err)
		connection.SetMaxOpenConns(8)
	}
	results := make(chan error, 8)
	for range 8 {
		go func() { results <- model.InitializeCNYPlans() }()
	}
	successes := 0
	for range 8 {
		if <-results == nil {
			successes++
		}
	}
	require.Equal(t, 1, successes)
	var count int64
	require.NoError(t, model.DB.Model(&model.SubscriptionPlan{}).Where("currency = ?", "CNY").Count(&count).Error)
	require.Equal(t, int64(6), count)
}

func TestBillingUpgradeMath(t *testing.T) {
	const start int64 = 1700000000
	week := model.BillingWeekSeconds
	// A previous paid upgrade may already carry more than four future weeks.
	// The residual must not be capped at one new 28-day period.
	extended := model.UserSubscription{StartTime: start, EndTime: start + 7*week, PurchasePriceCents: 179900, Status: "active"}
	value, err := model.CalculateBillingUpgrade(&extended, 259900, start)
	require.NoError(t, err)
	require.Equal(t, int64(6), value.RemainingWeeks)
	require.Greater(t, value.ExtensionSeconds, model.BillingPeriodSeconds)
	for _, test := range []struct {
		name    string
		elapsed int64
		weeks   int64
		valid   bool
	}{
		{"activation excludes first week", 0, 3, true},
		{"just before reset", week - 1, 3, true},
		{"exact reset excludes new current week", week, 2, true},
		{"third week", 2*week + 1, 1, true},
		{"last week", 3 * week, 0, true},
		{"expiry", 4 * week, 0, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			sub := model.UserSubscription{StartTime: start, EndTime: start + 4*week, PurchasePriceCents: 39900, Status: "active"}
			value, err := model.CalculateBillingUpgrade(&sub, 89900, start+test.elapsed)
			if !test.valid {
				require.Error(t, err)
				return
			}
			require.NoError(t, err)
			require.Equal(t, test.weeks, value.RemainingWeeks)
			require.Equal(t, int64(39900)*test.weeks/4, value.ResidualCents)
			require.Equal(t, value.ResidualCents*model.BillingPeriodSeconds/89900, value.ExtensionSeconds)
			_, err = model.CalculateBillingUpgrade(&sub, 39900, start+test.elapsed)
			require.Error(t, err, "same price is not an upgrade")
		})
	}
}

func billingFixture(t *testing.T) (model.User, []model.SubscriptionPlan) {
	t.Helper()
	setupTeamDatabase(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Option{}))
	for range 2 {
		require.NoError(t, model.DB.AutoMigrate(&model.BillingOrder{}))
	}
	require.NoError(t, model.InitializeCNYPlans())
	require.Error(t, model.InitializeCNYPlans(), "initialization must not overwrite admin adjustments")
	var plans []model.SubscriptionPlan
	require.NoError(t, model.DB.Where("currency = ?", "CNY").Order("price_amount asc").Find(&plans).Error)
	require.Len(t, plans, 6)
	quota, err := model.CNYCentsToQuota(1000000)
	require.NoError(t, err)
	user := model.User{Username: "billing-buyer", AffCode: "billing-buyer", Group: "default", Status: common.UserStatusEnabled, Quota: quota}
	require.NoError(t, model.DB.Create(&user).Error)
	return user, plans
}

func TestBillingCheckoutLifecycle(t *testing.T) {
	user, plans := billingFixture(t)
	quote := func(kind string, plan, old int, key string) *model.BillingOrder {
		order, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: kind, PlanId: plan, OldSubscriptionId: old, IdempotencyKey: key})
		require.NoError(t, err)
		return order
	}
	order := quote("subscription", plans[2].Id, 0, "first-order-unique-key")
	duplicate := quote("subscription", plans[2].Id, 0, "first-order-unique-key")
	require.Equal(t, order.Id, duplicate.Id)
	_, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[3].Id, IdempotencyKey: "first-order-unique-key"})
	require.Error(t, err)
	// Catalog editing after quoting must not change the purchase or its residual.
	require.NoError(t, model.DB.Model(&model.SubscriptionPlan{}).Where("id = ?", plans[2].Id).Updates(map[string]any{"price_amount": 499, "weekly_amount": plans[2].WeeklyAmount * 2, "total_amount": plans[2].TotalAmount * 2}).Error)
	paid, err := model.FulfillBillingOrder(user.Id, order.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	var old model.UserSubscription
	require.NoError(t, model.DB.First(&old, paid.SubscriptionId).Error)
	require.EqualValues(t, 39900, old.PurchasePriceCents)
	require.Equal(t, plans[2].WeeklyAmount, old.WeeklyAmount)
	var after model.User
	require.NoError(t, model.DB.First(&after, user.Id).Error)
	charge, err := model.CNYCentsToQuota(39900)
	require.NoError(t, err)
	require.Equal(t, user.Quota-charge, after.Quota)
	_, err = model.FulfillBillingOrder(user.Id, order.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(&after, user.Id).Error)
	require.Equal(t, user.Quota-charge, after.Quota)
	_, err = model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[3].Id, IdempotencyKey: "no-stacked-subscription"})
	require.Error(t, err)
	// Move original subscription into its second week.
	now := model.GetDBTimestamp()
	start := now - model.BillingWeekSeconds - 60
	require.NoError(t, model.DB.Model(&old).Updates(map[string]any{"start_time": start, "end_time": start + model.BillingPeriodSeconds, "weekly_reset_at": start + 2*model.BillingWeekSeconds}).Error)
	upgrade := quote("upgrade", plans[3].Id, old.Id, "upgrade-order-unique-key")
	require.EqualValues(t, 2, upgrade.RemainingWeeks)
	require.EqualValues(t, 19950, upgrade.ResidualCents)
	upgraded, err := model.FulfillBillingOrder(user.Id, upgrade.TradeNo, "balance", 0, false)
	require.NoError(t, err)
	var next model.UserSubscription
	require.NoError(t, model.DB.First(&next, upgraded.SubscriptionId).Error)
	require.NoError(t, model.DB.First(&old, old.Id).Error)
	require.Equal(t, "upgraded", old.Status)
	require.Equal(t, model.BillingPeriodSeconds+upgrade.ExtensionSeconds, next.EndTime-next.StartTime)
	next.RefreshWeeklyWindow(next.StartTime + model.BillingPeriodSeconds)
	require.Less(t, next.WeeklyLimit(), next.WeeklyAmount, "fractional extension cannot grant a full week")
	require.Positive(t, next.WeeklyLimit())
	_, err = model.FulfillBillingOrder(user.Id+1, upgrade.TradeNo, "balance", 0, false)
	require.Error(t, err)
	// Editing quota reset later cannot replenish a purchased plan's total.
	require.NoError(t, model.DB.Model(&model.SubscriptionPlan{}).Where("id = ?", next.PlanId).Update("quota_reset_period", "daily").Error)
	require.NoError(t, model.DB.Model(&next).Updates(map[string]any{"start_time": model.GetDBTimestamp() - 2*86400, "amount_used": 15}).Error)
	_, err = model.PreConsumeUserSubscription("paid-plan-reservation", user.Id, "gpt-6-sol", 0, 10)
	require.NoError(t, err)
	require.NoError(t, model.DB.First(&next, next.Id).Error)
	require.EqualValues(t, 25, next.AmountUsed)
	// Old requests settle on the old subscription, never against the new one.
	_, err = model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "upgrade", PlanId: plans[4].Id, OldSubscriptionId: old.Id, IdempotencyKey: "old-sub-cannot-upgrade"})
	require.Error(t, err)
}

func TestBillingPaygoAndDelayedPayment(t *testing.T) {
	user, plans := billingFixture(t)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		db, err := model.DB.DB()
		require.NoError(t, err)
		db.SetMaxOpenConns(8)
	}
	for index, amount := range []float64{-50, 0, 0.001, 1.001, 10000.01, 9223372036854775807, math.NaN(), math.Inf(1)} {
		_, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "paygo", AmountCNY: amount, IdempotencyKey: fmt.Sprintf("invalid-amount-%d-key", index)})
		require.Error(t, err)
	}
	for index, tc := range []struct {
		amount       float64
		cents, quota int64
	}{{0.01, 1, 714}, {1.23, 123, 87857}, {49, 4900, 3500000}, {51, 5100, 3642857}} {
		req := model.BillingCheckoutRequest{Kind: "paygo", AmountCNY: tc.amount, IdempotencyKey: fmt.Sprintf("small-paygo-order-%d-key", index)}
		order, err := model.CreateBillingCheckout(user.Id, req)
		require.NoError(t, err)
		require.Equal(t, tc.cents, order.PriceCents)
		require.Equal(t, tc.quota, order.CreditedQuota)
		retry, err := model.CreateBillingCheckout(user.Id, req)
		require.NoError(t, err)
		require.Equal(t, order.Id, retry.Id)
		_, err = model.ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
		require.NoError(t, err)
		_, err = model.FulfillBillingOrder(user.Id, order.TradeNo, "epay:alipay", tc.cents, true)
		require.NoError(t, err)
	}
	require.NoError(t, model.DB.First(&user, user.Id).Error)
	order, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "paygo", AmountCNY: 50, IdempotencyKey: "paygo-order-unique-key"})
	require.NoError(t, err)
	_, err = model.ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
	require.NoError(t, err)
	_, err = model.FulfillBillingOrder(user.Id, order.TradeNo, "epay:alipay", 4999, true)
	require.Error(t, err)
	var wg sync.WaitGroup
	errorsCh := make(chan error, 8)
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := model.FulfillBillingOrder(user.Id, order.TradeNo, "epay:alipay", 5000, true)
			errorsCh <- err
		}()
	}
	wg.Wait()
	close(errorsCh)
	for err := range errorsCh {
		require.NoError(t, err)
	}
	var after model.User
	require.NoError(t, model.DB.First(&after, user.Id).Error)
	require.Equal(t, user.Quota+int(order.CreditedQuota), after.Quota)
	// The bank has taken money for an expired quote: credit it instead of
	// resurrecting expired upgrade economics or losing the payment.
	sub, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "subscription", PlanId: plans[0].Id, IdempotencyKey: "expired-sub-payment-key"})
	require.NoError(t, err)
	_, err = model.ClaimBillingEpay(user.Id, sub.TradeNo, "epay:wxpay")
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(sub).Update("expires_at", model.GetDBTimestamp()-1).Error)
	paid, err := model.FulfillBillingOrder(user.Id, sub.TradeNo, "epay:wxpay", 9900, true)
	require.NoError(t, err)
	require.Equal(t, "wallet_credited", paid.Status)
	require.Zero(t, paid.SubscriptionId)
}

func TestBillingGatewayVerification(t *testing.T) {
	user, _ := billingFixture(t)
	oldURL, oldID, oldKey, oldClient := operation_setting.PayAddress, operation_setting.EpayId, operation_setting.EpayKey, billingGatewayHTTPClient
	t.Cleanup(func() {
		operation_setting.PayAddress, operation_setting.EpayId, operation_setting.EpayKey, billingGatewayHTTPClient = oldURL, oldID, oldKey, oldClient
	})
	operation_setting.EpayId, operation_setting.EpayKey = "10001", "test-only-merchant-key"
	order, err := model.CreateBillingCheckout(user.Id, model.BillingCheckoutRequest{Kind: "paygo", AmountCNY: 50, IdempotencyKey: "gateway-test-order-key"})
	require.NoError(t, err)
	_, err = model.ClaimBillingEpay(user.Id, order.TradeNo, "epay:alipay")
	require.NoError(t, err)
	queryAmount := "49.00"
	queryStatus := http.StatusOK
	queryBody := ""
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, order.TradeNo, r.URL.Query().Get("out_trade_no"))
		w.Header().Set("Location", "/redirect-must-not-be-followed")
		w.WriteHeader(queryStatus)
		if queryBody != "" {
			_, _ = io.WriteString(w, queryBody)
			return
		}
		_, _ = fmt.Fprintf(w, `{"code":1,"status":1,"pid":10001,"out_trade_no":%q,"type":"alipay","money":%q}`, order.TradeNo, queryAmount)
	}))
	defer server.Close()
	operation_setting.PayAddress = server.URL
	billingGatewayHTTPClient = server.Client()
	billingGatewayHTTPClient.CheckRedirect = oldClient.CheckRedirect
	router := gin.New()
	router.GET("/notify", BillingEpayNotify)
	router.GET("/return", BillingEpayReturn)
	params := epay.GenerateParams(map[string]string{"pid": "10001", "out_trade_no": order.TradeNo, "trade_no": "provider-123", "money": "50.00", "type": "alipay", "trade_status": "TRADE_SUCCESS"}, operation_setting.EpayKey)
	values := url.Values{}
	for k, v := range params {
		values.Set(k, v)
	}
	call := func(path string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		return w
	}
	require.Equal(t, "fail", call("/notify?"+values.Encode()).Body.String(), "signed callback alone cannot override mismatched gateway query")
	queryAmount = "50.00"
	for _, status := range []int{http.StatusServiceUnavailable, http.StatusFound} {
		queryStatus = status
		require.Equal(t, "fail", call("/notify?"+values.Encode()).Body.String())
	}
	queryStatus = http.StatusOK
	for _, body := range []string{"not-json", strings.Repeat(" ", 32769)} {
		queryBody = body
		require.Equal(t, "fail", call("/notify?"+values.Encode()).Body.String())
	}
	queryBody = ""
	for _, invalid := range []struct{ field, value string }{
		{"sign", "forged-signature"}, {"money", "49.99"}, {"pid", "10002"},
		{"out_trade_no", "unknown-order"}, {"type", "wxpay"}, {"trade_status", "WAIT_BUYER_PAY"},
	} {
		modified := map[string]string{}
		for key := range values {
			if key != "sign" && key != "sign_type" {
				modified[key] = values.Get(key)
			}
		}
		modified[invalid.field] = invalid.value
		signed := epay.GenerateParams(modified, operation_setting.EpayKey)
		if invalid.field == "sign" {
			signed["sign"] = invalid.value
		}
		encoded := url.Values{}
		for key, value := range signed {
			encoded.Set(key, value)
		}
		require.Equal(t, "fail", call("/notify?"+encoded.Encode()).Body.String(), invalid.field)
	}
	require.Equal(t, "fail", call("/notify?"+values.Encode()+"&money=50.00").Body.String(), "duplicate parameters rejected")
	require.Equal(t, http.StatusSeeOther, call("/return?"+values.Encode()).Code)
	unpaid, err := model.GetBillingOrder(user.Id, order.TradeNo)
	require.NoError(t, err)
	require.Equal(t, "pending", unpaid.Status)
	require.Equal(t, "success", call("/notify?"+values.Encode()).Body.String())
	require.Equal(t, "success", call("/notify?"+values.Encode()).Body.String())
	require.Equal(t, "fail", call("/notify?"+strings.Replace(values.Encode(), "pid=10001", "pid=10002", 1)).Body.String())
}
