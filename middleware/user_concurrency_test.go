package middleware

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
)

func TestUserConcurrencyIsolationAndRelease(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	started, release, done := make(chan struct{}), make(chan struct{}), make(chan struct{})
	router.Use(func(c *gin.Context) { id, _ := strconv.Atoi(c.GetHeader("User")); c.Set("id", id) })
	router.Use(userConcurrencyLimit(1))
	router.POST("/", func(c *gin.Context) {
		if c.GetHeader("Hold") == "yes" {
			close(started)
			<-release
		}
		c.Status(http.StatusOK)
	})
	call := func(user, hold string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/", nil)
		req.Header.Set("User", user)
		req.Header.Set("Hold", hold)
		response := httptest.NewRecorder()
		router.ServeHTTP(response, req)
		return response
	}
	go func() { call("991", "yes"); close(done) }()
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("first request did not start")
	}
	blocked := call("991", "")
	assert.Equal(t, http.StatusTooManyRequests, blocked.Code)
	assert.Equal(t, "5", blocked.Header().Get("Retry-After"))
	assert.Contains(t, blocked.Body.String(), "user_concurrency_limit")
	assert.Equal(t, http.StatusOK, call("992", "").Code)
	assert.Equal(t, http.StatusUnauthorized, call("", "").Code)
	close(release)
	<-done
	assert.Equal(t, http.StatusOK, call("991", "").Code)
}

func TestUserConcurrencyPreservesExemptImageClient(t *testing.T) {
	t.Setenv("RELAY_USER_CONCURRENCY", "1")
	t.Setenv("RELAY_CONCURRENCY_EXEMPT_IDS", "993")
	userRequests.Lock()
	userRequests.active[993] = 1
	userRequests.Unlock()
	t.Cleanup(func() {
		userRequests.Lock()
		delete(userRequests.active, 993)
		userRequests.Unlock()
	})
	router := gin.New()
	router.Use(func(c *gin.Context) { c.Set("id", 993) }, UserConcurrencyLimit())
	router.POST("/", func(c *gin.Context) { c.Status(http.StatusOK) })
	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/", nil))
	assert.Equal(t, http.StatusOK, response.Code)
}
