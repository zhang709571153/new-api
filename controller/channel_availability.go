package controller

import (
	"net/http"
	"strconv"
	"time"

	"github.com/QuantumNous/new-api/model"
	perfmetrics "github.com/QuantumNous/new-api/pkg/perf_metrics"
	"github.com/QuantumNous/new-api/setting/perf_metrics_setting"
	"github.com/gin-gonic/gin"
)

func GetChannelAvailability(c *gin.Context) {
	hours := 24
	if raw := c.Query("hours"); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value < 1 || value > 168 {
			c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "hours must be between 1 and 168"})
			return
		}
		hours = value
	}
	now := time.Now()
	rows, err := model.GetChannelAvailability(hours, now)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"success": false, "message": "Unable to load channel availability"})
		return
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"success": true, "data": gin.H{"channels": rows, "hours": hours, "observed_at": now.Unix(), "collection_enabled": perf_metrics_setting.GetSetting().Enabled, "dropped_samples": perfmetrics.ChannelSamplesDropped()}})
}
