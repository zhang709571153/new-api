package router

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"testing/fstest"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestClientDownloadPreservesRangesAndLengthWithCompressionRequested(t *testing.T) {
	router := gin.New()
	handler := clientDownloadHandler(fstest.MapFS{
		"runtime/client.tar.gz": &fstest.MapFile{Data: []byte("0123456789")},
	})
	router.GET("/downloads/realyu/*file", handler)
	router.HEAD("/downloads/realyu/*file", handler)
	for _, method := range []string{http.MethodGet, http.MethodHead} {
		request := httptest.NewRequest(method, "/downloads/realyu/runtime/client.tar.gz?v=fixture", nil)
		request.Header.Set("Accept-Encoding", "gzip, br")
		if method == http.MethodGet {
			request.Header.Set("Range", "bytes=3-6")
		}
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		require.Empty(t, response.Header().Get("Content-Encoding"))
		require.Contains(t, response.Header().Get("Cache-Control"), "no-transform")
		if method == http.MethodGet {
			require.Equal(t, http.StatusPartialContent, response.Code)
			require.Equal(t, "3456", response.Body.String())
			require.Equal(t, "4", response.Header().Get("Content-Length"))
			require.Equal(t, "bytes 3-6/10", response.Header().Get("Content-Range"))
		} else {
			require.Equal(t, "10", response.Header().Get("Content-Length"))
			require.Empty(t, response.Body.String())
		}
	}
}

func TestClientDownloadDoesNotCacheMutableScriptsOrReturnHTMLForMissingAssets(t *testing.T) {
	router := gin.New()
	router.GET("/downloads/realyu/*file", clientDownloadHandler(fstest.MapFS{
		"setup.sh": &fstest.MapFile{Data: []byte("#!/bin/sh\necho ready\n")},
	}))
	for _, path := range []string{"setup.sh", "missing.sh", "../private", ""} {
		response := httptest.NewRecorder()
		router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/downloads/realyu/"+path, nil))
		if path == "setup.sh" {
			require.Equal(t, http.StatusOK, response.Code)
			require.Contains(t, response.Header().Get("Cache-Control"), "no-store")
		} else {
			require.Equal(t, http.StatusNotFound, response.Code)
			require.NotContains(t, response.Body.String(), "<html")
		}
	}
}
