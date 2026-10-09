package controller

import (
	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"net/http"
)

// The resolved relay credential stays server-side and is checked by TokenAuth.
func PlaygroundIdentity(c *gin.Context) {
	if _, ok := middleware.GetSessionAuthIdentity(c); !ok || c.GetHeader("Sec-Fetch-Site") == "cross-site" {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": gin.H{"message": "请登录网站后操作", "type": "access_denied"}})
		return
	}
	token, err := model.GetPlaygroundKey(c.GetInt("id"), c.Query("scope"))
	if err != nil || token == nil {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": gin.H{"message": "当前工作区密钥不可用", "type": "access_denied"}})
		return
	}
	c.Set("playground_real_key", true)
	c.Request.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(token))
	c.Header("Cache-Control", "no-store")
	c.Next()
}

func GetPlaygroundContext(c *gin.Context) {
	result, err := service.GetPlaygroundContext(c.GetInt("id"), c.Query("scope"))
	workspaceResult(c, result, err, "", 0)
}

func Playground(c *gin.Context) {
	if !c.GetBool("playground_real_key") || c.GetInt("token_id") <= 0 {
		common.ApiError(c, model.ErrWorkspaceAccess)
		return
	}
	Relay(c, types.RelayFormatOpenAI)
}
