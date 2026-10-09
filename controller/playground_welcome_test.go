package controller

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func welcomeTestUser(t *testing.T, name string, eligible bool) *model.User {
	t.Helper()
	user := &model.User{Username: name, AffCode: name, Role: common.RoleCommonUser, Status: common.UserStatusEnabled, AuthVersion: 1, Group: "default"}
	require.NoError(t, model.DB.Create(user).Error)
	if eligible {
		require.NoError(t, model.DB.Create(&model.WelcomeCredit{UserID: user.Id, Source: "registration", Status: "pending", CreatedAt: common.GetTimestamp()}).Error)
	}
	return user
}

func TestWelcomeCreditLedger(t *testing.T) {
	setupTeamDatabase(t)
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	policy, err := model.GetWelcomePolicy()
	require.NoError(t, err)
	require.False(t, policy.Enabled)
	policy.Enabled = true
	require.NoError(t, model.UpdateWelcomePolicy(*policy))
	quota, err := model.CNYCentsToQuota(500)
	require.NoError(t, err)
	require.Equal(t, 357142, quota)
	user := welcomeTestUser(t, "gift-main", true)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		db, _ := model.DB.DB()
		db.SetMaxOpenConns(8)
	}
	var wg sync.WaitGroup
	errors := make(chan error, 12)
	for range 12 {
		wg.Go(func() { _, err := model.GrantWelcomeCredit(user.Id, "browser-a", "ip-a", 0, ""); errors <- err })
	}
	wg.Wait()
	close(errors)
	for err := range errors {
		require.NoError(t, err)
	}
	require.NoError(t, model.DB.First(user, user.Id).Error)
	require.Equal(t, quota, user.Quota)
	p, err := model.GetWelcomePolicy()
	require.NoError(t, err)
	require.EqualValues(t, 500, p.TotalSpentCents)
	var count int64
	require.NoError(t, model.DB.Model(&model.WelcomeBrowser{}).Count(&count).Error)
	require.EqualValues(t, 1, count)
	second := welcomeTestUser(t, "gift-browser", true)
	credit, err := model.GrantWelcomeCredit(second.Id, "browser-a", "ip-b", 0, "")
	require.NoError(t, err)
	require.Equal(t, "browser_used", credit.Reason)
	for i := range 2 {
		u := welcomeTestUser(t, fmt.Sprintf("gift-ip-%d", i), true)
		c, e := model.GrantWelcomeCredit(u.Id, fmt.Sprintf("browser-%d", i), "ip-a", 0, "")
		require.NoError(t, e)
		require.Equal(t, "granted", c.Status)
	}
	fourth := welcomeTestUser(t, "gift-ip-over", true)
	credit, err = model.GrantWelcomeCredit(fourth.Id, "browser-4", "ip-a", 0, "")
	require.NoError(t, err)
	require.Equal(t, "ip_limit", credit.Reason)
	// A manual review bypasses browser/IP heuristics, but never account idempotency.
	credit, err = model.GrantWelcomeCredit(second.Id, "", "", 99, "verified shared device")
	require.NoError(t, err)
	require.Equal(t, "granted", credit.Status)
	_, err = model.GrantWelcomeCredit(second.Id, "", "", 99, "repeat review")
	require.NoError(t, err)
	p, err = model.GetWelcomePolicy()
	require.NoError(t, err)
	require.EqualValues(t, 2000, p.TotalSpentCents)
	p.DailyLimitCents = 2000
	require.NoError(t, model.UpdateWelcomePolicy(*p))
	fifth := welcomeTestUser(t, "gift-budget", true)
	credit, err = model.GrantWelcomeCredit(fifth.Id, "browser-5", "ip-z", 0, "")
	require.NoError(t, err)
	require.Equal(t, "budget_reached", credit.Reason)
	// A fixed backfill cohort can be credited once, even after later exhaustion.
	old := welcomeTestUser(t, "gift-old", false)
	require.NoError(t, model.EnrollWelcomeBackfill([]int{old.Id}))
	credit, err = model.GrantWelcomeCredit(old.Id, "", "", 99, "approved existing cohort")
	require.NoError(t, err)
	require.Equal(t, "granted", credit.Status)
	require.NoError(t, model.DB.Model(old).UpdateColumn("quota", 0).Error)
	require.NoError(t, model.EnrollWelcomeBackfill([]int{old.Id}))
	_, err = model.GrantWelcomeCredit(old.Id, "", "", 99, "repeat exhausted balance")
	require.NoError(t, err)
	require.NoError(t, model.DB.First(old, old.Id).Error)
	require.Zero(t, old.Quota)
	changed := welcomeTestUser(t, "gift-changed", false)
	require.NoError(t, model.EnrollWelcomeBackfill([]int{changed.Id}))
	require.NoError(t, model.DB.Model(changed).UpdateColumn("quota", 1).Error)
	credit, err = model.GrantWelcomeCredit(changed.Id, "", "", 99, "balance changed since freeze")
	require.NoError(t, err)
	require.Equal(t, "skipped", credit.Status)
	// Repeated migrations preserve monetary records and campaign counters.
	require.NoError(t, model.DB.AutoMigrate(&model.WelcomePolicy{}, &model.WelcomeCredit{}, &model.WelcomeBrowser{}))
	require.NoError(t, model.InitializeWelcomePolicy())
	p, err = model.GetWelcomePolicy()
	require.NoError(t, err)
	require.EqualValues(t, 2500, p.TotalSpentCents)
	p.TotalLimitCents = 2500
	p.DailyLimitCents = 10000
	require.NoError(t, model.UpdateWelcomePolicy(*p))
	credit, err = model.GrantWelcomeCredit(fifth.Id, "", "", 99, "manual cannot exceed campaign")
	require.NoError(t, err)
	require.Equal(t, "budget_reached", credit.Reason)
	require.NoError(t, model.DB.First(fifth, fifth.Id).Error)
	require.Zero(t, fifth.Quota)
}

func TestWelcomeParallelLimits(t *testing.T) {
	setupTeamDatabase(t)
	if !common.UsingMainDatabase(common.DatabaseTypeSQLite) {
		db, _ := model.DB.DB()
		db.SetMaxOpenConns(8)
	}
	policy, _ := model.GetWelcomePolicy()
	policy.Enabled = true
	require.NoError(t, model.UpdateWelcomePolicy(*policy))
	for caseID, limit := range []int{3, 1, 2} {
		if caseID == 2 {
			policy.TotalLimitCents = 3000
			require.NoError(t, model.UpdateWelcomePolicy(*policy))
		}
		users := make([]*model.User, 8)
		for i := range users {
			users[i] = welcomeTestUser(t, fmt.Sprintf("race-%d-%d", caseID, i), true)
		}
		type result struct {
			credit *model.WelcomeCredit
			err    error
		}
		results := make(chan result, 8)
		var wg sync.WaitGroup
		for i, u := range users {
			wg.Go(func() {
				browser, ip := fmt.Sprintf("race-browser-%d-%d", caseID, i), fmt.Sprintf("race-ip-%d-%d", caseID, i)
				if caseID == 0 {
					ip = "shared-ip"
				}
				if caseID == 1 {
					browser = "shared-browser"
				}
				c, e := model.GrantWelcomeCredit(u.Id, browser, ip, 0, "")
				results <- result{c, e}
			})
		}
		wg.Wait()
		close(results)
		granted := 0
		for result := range results {
			require.NoError(t, result.err)
			if result.credit.Status == "granted" {
				granted++
			}
		}
		require.Equal(t, limit, granted)
	}
	var total int64
	require.NoError(t, model.DB.Model(&model.User{}).Select("COALESCE(SUM(quota), 0)").Scan(&total).Error)
	require.EqualValues(t, 6*357142, total)
}

func TestWelcomeSecurityAndRegistration(t *testing.T) {
	setupTeamDatabase(t)
	oldSecure := common.SessionCookieSecure
	common.SessionCookieSecure = true
	t.Cleanup(func() { common.SessionCookieSecure = oldSecure })
	oldRegister, oldPassword, oldEmail := common.RegisterEnabled, common.PasswordRegisterEnabled, common.EmailVerificationEnabled
	common.RegisterEnabled, common.PasswordRegisterEnabled, common.EmailVerificationEnabled = true, true, false
	t.Cleanup(func() {
		common.RegisterEnabled, common.PasswordRegisterEnabled, common.EmailVerificationEnabled = oldRegister, oldPassword, oldEmail
	})
	p, _ := model.GetWelcomePolicy()
	p.Enabled = true
	require.NoError(t, model.UpdateWelcomePolicy(*p))
	router := gin.New()
	require.NoError(t, router.SetTrustedProxies(nil))
	router.GET("/offer", GetWelcomeOffer)
	router.POST("/register", middleware.WelcomeRegistrationRateLimit(), Register)
	router.POST("/claim", middleware.UserAuth(), WorkspaceSessionRequired, ClaimWelcomeCredit)
	router.PUT("/admin", middleware.RootAuth(), WorkspaceSessionRequired, UpdateWelcomeAdmin)
	call := func(method, path, body, token string, cookie *http.Cookie) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.RemoteAddr = "192.0.2.22:1234"
		req.Header.Set("Content-Type", "application/json")
		if cookie != nil {
			req.AddCookie(cookie)
		}
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		return res
	}
	res := call("GET", "/offer", "", "", nil)
	cookies := res.Result().Cookies()
	require.Len(t, cookies, 1)
	cookie := cookies[0]
	require.True(t, cookie.Secure && cookie.HttpOnly)
	require.Equal(t, http.SameSiteLaxMode, cookie.SameSite)
	require.Equal(t, "__Host-realyu_trial", cookie.Name)
	for i := range 2 {
		body := fmt.Sprintf(`{"username":"welcome-reg-%d","password":"Local-test-password-894!","quota":999999,"role":100,"welcome_eligible":false}`, i)
		require.Contains(t, call("POST", "/register", body, "", cookie).Body.String(), `"success":true`)
	}
	var first, second model.User
	require.NoError(t, model.DB.First(&first, "username = ?", "welcome-reg-0").Error)
	require.NoError(t, model.DB.First(&second, "username = ?", "welcome-reg-1").Error)
	require.Equal(t, common.RoleCommonUser, first.Role)
	require.Equal(t, 357142, first.Quota)
	require.Zero(t, second.Quota)
	session, err := service.CreateLoginSession(second.Id, "password", "192.0.2.22", "test")
	require.NoError(t, err)
	require.Contains(t, call("POST", "/claim", "{}", session.AccessToken, cookie).Body.String(), "browser_used")
	bad := *cookie
	bad.Value = "tampered." + bad.Value
	require.Contains(t, call("POST", "/claim", "{}", session.AccessToken, &bad).Body.String(), "browser_required")
	require.Equal(t, 401, call("POST", "/claim", "{}", "", cookie).Code)
	require.Equal(t, 403, call("PUT", "/admin", "{}", session.AccessToken, cookie).Code)
	// IPv6 privacy addresses share a /64. Untrusted forwarded IPs are ignored.
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest("GET", "/", nil)
	c.Request.RemoteAddr = "[2001:db8:abcd:1::1]:5"
	one := welcomeIP(c)
	c.Request.RemoteAddr = "[2001:db8:abcd:1::aaaa]:5"
	require.Equal(t, one, welcomeIP(c))
	c.Request.RemoteAddr = "[2001:db8:abcd:2::1]:5"
	require.NotEqual(t, one, welcomeIP(c))
	for range 8 {
		call("POST", "/register", "{}", "", nil)
	}
	require.Equal(t, 429, call("POST", "/register", "{}", "", nil).Code)
}

func TestPlaygroundRealKeyFunding(t *testing.T) {
	setupTeamDatabase(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Ability{}))
	oldBatch := common.BatchUpdateEnabled
	common.BatchUpdateEnabled = false
	t.Cleanup(func() { common.BatchUpdateEnabled = oldBatch })
	t.Setenv("RELAY_USER_CONCURRENCY", "1")
	owner := welcomeTestUser(t, "pg-owner", false)
	member := welcomeTestUser(t, "pg-member", false)
	for _, u := range []*model.User{owner, member} {
		require.NoError(t, model.DB.Model(u).UpdateColumn("quota", 100000).Error)
		_, err := model.GetWorkspaceKey(u.Id, true)
		require.NoError(t, err)
	}
	workspaceTeam, err := model.CreateWorkspaceTeam(owner.Id, "Playground test")
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.WorkspaceTeamAccount{}).Where("team_id = ?", workspaceTeam.ID).Update("quota", 100000).Error)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(member.Id, invite))
	allowance := 10000
	require.NoError(t, model.UpdateWorkspaceMember(owner.Id, member.Id, &allowance, nil, nil))
	// Independent team funding requires an active weekly subscription; a
	// wallet balance alone is not an enabled overflow grant.
	teamSubscription, err := model.SetWorkspaceTeamSubscription(owner.Id, workspaceTeam.ID, 100000, 100000)
	require.NoError(t, err)
	personal, err := model.GetPlaygroundKey(member.Id, "personal")
	require.NoError(t, err)
	team, err := model.GetPlaygroundKey(member.Id, "team")
	require.NoError(t, err)
	for _, scope := range []string{"personal", "team"} {
		ctx, err := service.GetPlaygroundContext(member.Id, scope)
		require.NoError(t, err)
		require.Equal(t, "default", ctx.Group)
		require.True(t, ctx.CanChat)
		require.Equal(t, scope, ctx.Scope)
	}
	session, err := service.CreateLoginSession(member.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	router := gin.New()
	router.POST("/pg/chat/completions", middleware.UserAuth(), PlaygroundIdentity, middleware.TokenAuth(), middleware.WelcomeTrialLimit(), middleware.UserConcurrencyLimit(), func(c *gin.Context) {
		info := relaycommon.GenRelayInfoOpenAI(c, &dto.GeneralOpenAIRequest{})
		require.False(t, info.IsPlayground, "real key must never take virtual-token billing exemption")
		funding, apiErr := service.NewBillingSession(c, info, 100)
		if apiErr != nil {
			c.Status(apiErr.StatusCode)
			return
		}
		if c.Query("fail") == "1" {
			funding.Refund(c)
			funding.Refund(c)
			c.Status(502)
			return
		}
		require.NoError(t, funding.Settle(120))
		require.NoError(t, funding.Settle(120))
		c.Status(200)
	})
	call := func(scope, credential, query string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/pg/chat/completions?scope="+scope+query, strings.NewReader("{}"))
		req.Header.Set("Authorization", "Bearer "+credential)
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		return res
	}
	require.Equal(t, 200, call("personal", session.AccessToken, "").Code)
	require.NoError(t, model.DB.First(member, member.Id).Error)
	require.Equal(t, 99880, member.Quota)
	require.NoError(t, model.DB.First(personal, personal.Id).Error)
	require.Equal(t, 120, personal.UsedQuota)
	require.Equal(t, 200, call("team", session.AccessToken, "").Code)
	require.NoError(t, model.DB.First(owner, owner.Id).Error)
	require.Equal(t, 100000, owner.Quota)
	require.NoError(t, model.DB.First(team, team.Id).Error)
	require.Equal(t, allowance-120, team.RemainQuota)
	require.Equal(t, 502, call("team", session.AccessToken, "&fail=1").Code)
	require.Eventually(t, func() bool {
		var u model.WorkspaceTeamAccount
		var k model.Token
		var sub model.UserSubscription
		model.DB.First(&u, workspaceTeam.ID)
		model.DB.First(&k, team.Id)
		model.DB.First(&sub, teamSubscription.Id)
		return u.Quota == 100000 && sub.AmountUsed == 120 && k.RemainQuota == allowance-120
	}, 5*time.Second, 20*time.Millisecond)
	require.NotEqual(t, 200, call("personal", model.WorkspaceKeyText(personal), "").Code, "relay API key must not replace a website session")
	require.Equal(t, 403, call("other", session.AccessToken, "").Code)
	require.NoError(t, model.DB.Model(team).UpdateColumn("status", common.TokenStatusDisabled).Error)
	require.NotEqual(t, 200, call("team", session.AccessToken, "").Code)
	// Gift-only requests pass a nested regular limit once, then stop at six/minute.
	trial := welcomeTestUser(t, "pg-trial", true)
	p, _ := model.GetWelcomePolicy()
	p.Enabled = true
	require.NoError(t, model.UpdateWelcomePolicy(*p))
	_, err = model.GrantWelcomeCredit(trial.Id, "trial-browser", "trial-ip", 0, "")
	require.NoError(t, err)
	_, err = model.GetWorkspaceKey(trial.Id, true)
	require.NoError(t, err)
	trialSession, err := service.CreateLoginSession(trial.Id, "password", "127.0.0.1", "test")
	require.NoError(t, err)
	for range 6 {
		require.Equal(t, 200, call("personal", trialSession.AccessToken, "").Code)
	}
	require.Equal(t, 429, call("personal", trialSession.AccessToken, "").Code)
}
