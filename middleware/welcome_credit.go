package middleware

import (
	"net/http"
	"strconv"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

var welcomeRateLimiter common.InMemoryRateLimiter

func WelcomeRegistrationRateLimit() gin.HandlerFunc {
	welcomeRateLimiter.Init(time.Hour)
	return func(c *gin.Context) {
		if !welcomeRateLimiter.Request("welcome-register:"+c.ClientIP(), 10, 3600) {
			c.Header("Retry-After", "3600")
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"success": false, "message": "注册操作过于频繁，请稍后重试"})
			return
		}
		c.Next()
	}
}

// This host has one gateway process. A cluster deployment needs shared leases
// for request concurrency, while all gift uniqueness and budgets already live
// in the database and survive process restarts.
func WelcomeTrialLimit() gin.HandlerFunc {
	welcomeRateLimiter.Init(time.Hour)
	concurrency := userConcurrencyLimit(1)
	return func(c *gin.Context) {
		trial, err := model.IsWelcomeTrial(c.GetInt("id"))
		if err != nil {
			c.AbortWithStatusJSON(503, gin.H{"error": gin.H{"message": "暂时无法检查体验额度，请稍后重试", "type": "server_error"}})
			return
		}
		if !trial {
			c.Next()
			return
		}
		if !welcomeRateLimiter.Request("welcome-chat:"+strconv.Itoa(c.GetInt("id")), 6, 60) {
			c.Header("Retry-After", "60")
			c.AbortWithStatusJSON(429, gin.H{"error": gin.H{"message": "体验账号请求过于频繁，请稍后重试", "type": "rate_limit_error"}})
			return
		}
		concurrency(c)
	}
}
