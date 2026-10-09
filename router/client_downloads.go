package router

import (
	"io"
	"io/fs"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

// Keep client downloads outside dashboard gzip middleware. CDN range responses
// require the original Content-Length; already-compressed bundles must not be
// transformed. Mutable bootstrap scripts must not remain cached for a week.
func clientDownloadHandler(assets fs.FS) gin.HandlerFunc {
	return func(c *gin.Context) {
		name := strings.TrimPrefix(c.Param("file"), "/")
		c.Header("Cache-Control", "no-store, no-transform")
		if !fs.ValidPath(name) {
			c.AbortWithStatus(http.StatusNotFound)
			return
		}
		file, err := assets.Open(name)
		if err != nil {
			c.AbortWithStatus(http.StatusNotFound)
			return
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() {
			c.AbortWithStatus(http.StatusNotFound)
			return
		}
		content, ok := file.(io.ReadSeeker)
		if !ok {
			c.AbortWithStatus(http.StatusInternalServerError)
			return
		}
		if strings.HasSuffix(name, ".tar.gz") || strings.HasSuffix(name, ".exe") {
			c.Header("Cache-Control", "public, max-age=604800, no-transform")
			c.Header("Content-Type", "application/octet-stream")
		}
		http.ServeContent(c.Writer, c.Request, info.Name(), info.ModTime(), content)
	}
}
