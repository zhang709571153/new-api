package controller

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/netip"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

func welcomeDigest(value string) string {
	h := hmac.New(sha256.New, []byte(common.SessionSecret))
	h.Write([]byte("realyu-welcome-v1:" + value))
	return hex.EncodeToString(h.Sum(nil))
}

func welcomeBrowser(c *gin.Context, create bool) string {
	name := "realyu_trial"
	if common.SessionCookieSecure {
		name = "__Host-realyu_trial"
	}
	value, _ := c.Cookie(name)
	parts := strings.Split(value, ".")
	if len(parts) == 3 && len(parts[0]) == 64 {
		issued, err := strconv.ParseInt(parts[1], 10, 64)
		now := common.GetTimestamp()
		if err == nil && issued <= now+60 && issued > now-180*86400 && hmac.Equal([]byte(parts[2]), []byte(welcomeDigest("cookie:"+parts[0]+"."+parts[1]))) {
			return welcomeDigest("browser:" + parts[0])
		}
	}
	if !create {
		return ""
	}
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		return ""
	}
	id := hex.EncodeToString(random[:])
	payload := id + "." + strconv.FormatInt(common.GetTimestamp(), 10)
	http.SetCookie(c.Writer, &http.Cookie{Name: name, Value: payload + "." + welcomeDigest("cookie:"+payload), Path: "/", Secure: common.SessionCookieSecure, HttpOnly: true, SameSite: http.SameSiteLaxMode, MaxAge: 180 * 86400})
	return welcomeDigest("browser:" + id)
}

func welcomeIP(c *gin.Context) string {
	ip, err := netip.ParseAddr(c.ClientIP())
	if err != nil {
		return ""
	}
	ip = ip.Unmap()
	value := ip.String()
	if ip.Is6() {
		value = netip.PrefixFrom(ip, 64).Masked().String()
	}
	return welcomeDigest("ip:" + value)
}

func GetWelcomeOffer(c *gin.Context) {
	welcomeBrowser(c, true)
	policy, err := model.GetWelcomePolicy()
	if err != nil {
		common.ApiError(c, err)
		return
	}
	common.ApiSuccess(c, gin.H{"enabled": policy.Enabled, "amount_cents": policy.AmountCents})
}

func tryWelcomeGrant(c *gin.Context, userID int) {
	_, err := model.GrantWelcomeCredit(userID, welcomeBrowser(c, false), welcomeIP(c), 0, "")
	if err != nil {
		common.SysLog("Welcome grant deferred; the authenticated user can retry")
	}
}

func GetMyWelcomeCredit(c *gin.Context) {
	credit, err := model.GetWelcomeCredit(c.GetInt("id"))
	workspaceResult(c, credit, err, "", 0)
}

func ClaimWelcomeCredit(c *gin.Context) {
	credit, err := model.GrantWelcomeCredit(c.GetInt("id"), welcomeBrowser(c, false), welcomeIP(c), 0, "")
	workspaceResult(c, credit, err, "", 0)
}

func GetWelcomeAdmin(c *gin.Context) {
	policy, err := model.GetWelcomePolicy()
	if err != nil {
		common.ApiError(c, err)
		return
	}
	var credits []model.WelcomeCredit
	if err := model.DB.Order("created_at desc").Limit(200).Find(&credits).Error; err != nil {
		common.ApiError(c, err)
		return
	}
	common.ApiSuccess(c, gin.H{"policy": policy, "credits": credits})
}

func UpdateWelcomeAdmin(c *gin.Context) {
	var policy model.WelcomePolicy
	if err := common.DecodeJson(c.Request.Body, &policy); err != nil {
		common.ApiError(c, err)
		return
	}
	err := model.UpdateWelcomePolicy(policy)
	workspaceResult(c, nil, err, "welcome.policy.update", 0)
}

func ReviewWelcomeCredit(c *gin.Context) {
	var request struct {
		UserID int    `json:"user_id"`
		Reason string `json:"reason"`
	}
	if err := common.DecodeJson(c.Request.Body, &request); err != nil || request.UserID <= 0 || len(request.Reason) > 200 || len(strings.TrimSpace(request.Reason)) < 3 {
		c.AbortWithStatusJSON(400, gin.H{"success": false, "message": "请填写用户编号与复核原因"})
		return
	}
	credit, err := model.GrantWelcomeCredit(request.UserID, "", "", c.GetInt("id"), request.Reason)
	workspaceResult(c, credit, err, "welcome.review", request.UserID)
}

func EnrollWelcomeBackfill(c *gin.Context) {
	var request struct {
		UserIDs []int `json:"user_ids"`
	}
	if err := common.DecodeJson(c.Request.Body, &request); err != nil {
		common.ApiError(c, err)
		return
	}
	err := model.EnrollWelcomeBackfill(request.UserIDs)
	workspaceResult(c, nil, err, "welcome.backfill.enroll", 0)
}
