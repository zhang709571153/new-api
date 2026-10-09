package controller

import (
	"io"
	"net/http"
	"strconv"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

func CodexChannelLogin(c *gin.Context) {
	c.Header("Cache-Control", "no-store")
	identity, ok := middleware.GetSessionAuthIdentity(c)
	channelID, err := strconv.Atoi(c.Param("id"))
	if !ok || err != nil || channelID <= 0 {
		c.JSON(http.StatusForbidden, gin.H{"success": false, "message": "A dashboard session is required."})
		return
	}
	action := c.Param("action")
	if action == "" {
		action = "start"
	}
	var result *service.CodexChannelLoginView
	if action == "start" {
		// An active root dashboard session and same-account upstream login are required.
		if _, readErr := io.Copy(io.Discard, http.MaxBytesReader(c.Writer, c.Request.Body, 1024)); readErr != nil {
			c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "Invalid login attempt."})
			return
		}
		result, err = service.StartCodexChannelLogin(identity, channelID)
	} else {
		var body struct {
			ID string `json:"id"`
		}
		if common.DecodeJson(http.MaxBytesReader(c.Writer, c.Request.Body, 1024), &body) != nil || len(body.ID) != 36 {
			c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": "Invalid login attempt."})
			return
		}
		switch action {
		case "status":
			result, err = service.GetCodexChannelLogin(identity, channelID, body.ID)
		case "cancel":
			err = service.CancelCodexChannelLogin(identity, channelID, body.ID)
		case "complete":
			err = service.CompleteCodexChannelLogin(c.Request.Context(), identity, channelID, body.ID)
		default:
			c.JSON(http.StatusNotFound, gin.H{"success": false})
			return
		}
	}
	if err != nil {
		// The service only returns controlled messages; never expose process output or tokens.
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "message": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": result})
}
