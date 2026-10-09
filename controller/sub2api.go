package controller

import (
	"io"
	"net/http"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/tidwall/gjson"
)

func Sub2APIStatus(c *gin.Context) {
	enabled := service.Sub2APIDriverEnabled()
	data := gin.H{"enabled": enabled, "admin_url": service.Sub2APIAdminURL(), "configured": false}
	if enabled {
		cfg, err := service.LoadSub2APIConfig()
		if err != nil {
			data["reason"] = err.Error()
		} else {
			data["configured"], data["pool_count"], data["provisioning_enabled"] = true, len(cfg.Pools), cfg.Provision.Enabled
		}
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": data})
}

func Sub2APIWorkspaceStatus(c *gin.Context) {
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": service.GetSub2APIEnrollmentStatus(c.GetInt("id"), c.Query("scope"))})
}

func listSub2APICodexModels(c *gin.Context, modelNames, groups []string) {
	cfg, err := service.LoadSub2APIConfig()
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": gin.H{"type": "upstream_unavailable", "message": "Sub2API catalog is not configured"}})
		return
	}
	models := make([]common.RawMessage, 0)
	seen := make(map[string]bool)
	for _, pool := range cfg.Pools {
		channel, err := model.CacheGetChannel(pool.ChannelID)
		if err != nil || channel.Type != constant.ChannelTypeSub2API || channel.GetBaseURL() != pool.BaseURL {
			continue
		}
		allowed := make(map[string]bool)
		for _, name := range modelNames {
			for _, group := range groups {
				if model.IsChannelEnabledForGroupModel(group, name, pool.ChannelID) {
					allowed[name] = true
					break
				}
			}
		}
		if len(allowed) == 0 {
			continue
		}
		binding, err := service.ResolveSub2APIBinding(c.Request.Context(), service.Sub2APISubjectFromContext(c), pool.ChannelID, pool.BaseURL)
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": gin.H{"type": "upstream_unavailable", "message": "Sub2API customer catalog is unavailable"}})
			return
		}
		entries, err := service.FetchSub2APICodexModels(c.Request.Context(), binding, c.Query("client_version"), allowed)
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": gin.H{"type": "upstream_unavailable", "message": "Sub2API Codex catalog is unavailable"}})
			return
		}
		for _, entry := range entries {
			slug := gjson.GetBytes(entry, "slug").String()
			if !seen[slug] {
				models = append(models, entry)
				seen[slug] = true
			}
		}
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"models": models})
}

func RequireLegacyChannelManagement(c *gin.Context) {
	if service.Sub2APIDriverEnabled() {
		// Complete small authenticated management uploads before returning the
		// denial. Closing a socket with unread input can reset it on Windows,
		// hiding the 410 from clients. Bound both bytes and time for large/slow
		// bodies; none of the legacy handlers is invoked.
		if c.Request.Body != nil {
			controller := http.NewResponseController(c.Writer)
			_ = controller.SetReadDeadline(time.Now().Add(2 * time.Second))
			_, _ = io.Copy(io.Discard, io.LimitReader(c.Request.Body, 64<<10))
			_ = controller.SetReadDeadline(time.Time{})
			if c.Request.ContentLength < 0 || c.Request.ContentLength > 64<<10 {
				c.Header("Connection", "close")
			}
		}
		c.AbortWithStatusJSON(http.StatusGone, gin.H{"success": false, "message": "Upstream accounts are managed in Sub2API", "code": "sub2api_managed"})
		return
	}
	c.Next()
}
