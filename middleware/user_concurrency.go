package middleware

import (
	"net/http"
	"strconv"
	"strings"
	"sync"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

// A single gateway process shares slots across all keys belonging to a user.
// Scale out behind sticky user routing or a distributed limiter, not by assuming
// this process-local guard is a cluster-wide concurrency cap.
var userRequests = struct {
	sync.Mutex
	active map[int]int
}{active: make(map[int]int)}

func UserConcurrencyLimit() gin.HandlerFunc {
	limit := userConcurrencyLimit(common.GetEnvOrDefault("RELAY_USER_CONCURRENCY", 0))
	exempt := map[int]bool{}
	for value := range strings.SplitSeq(common.GetEnvOrDefaultString("RELAY_CONCURRENCY_EXEMPT_IDS", ""), ",") {
		if id, err := strconv.Atoi(strings.TrimSpace(value)); err == nil && id > 0 {
			exempt[id] = true
		}
	}
	return func(c *gin.Context) {
		if exempt[c.GetInt("id")] && c.GetInt("workspace_user_id") == 0 {
			c.Next()
			return
		}
		limit(c)
	}
}

func userConcurrencyLimit(limit int) gin.HandlerFunc {
	return func(c *gin.Context) {
		if limit <= 0 || c.GetBool("user_concurrency_acquired") {
			c.Next()
			return
		}
		id := c.GetInt("id")
		if memberID := c.GetInt("workspace_user_id"); memberID > 0 {
			id = memberID
		}
		if id <= 0 {
			c.AbortWithStatus(http.StatusUnauthorized)
			return
		}
		userRequests.Lock()
		if userRequests.active[id] >= limit {
			userRequests.Unlock()
			c.Header("Retry-After", "5")
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"error": gin.H{
				"type": "rate_limit_error", "code": "user_concurrency_limit",
				"message": "Your concurrent request limit is reached. Retry after an active request finishes.",
			}})
			return
		}
		userRequests.active[id]++
		c.Set("user_concurrency_acquired", true)
		userRequests.Unlock()
		defer func() {
			c.Set("user_concurrency_acquired", false)
			userRequests.Lock()
			userRequests.active[id]--
			if userRequests.active[id] == 0 {
				delete(userRequests.active, id)
			}
			userRequests.Unlock()
		}()
		c.Next()
	}
}
