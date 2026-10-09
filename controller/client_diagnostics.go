package controller

import (
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
)

// This anonymous endpoint must work even when key validation fails. Reports are
// untrusted client observations, not authenticated audit events. Never accept
// arbitrary logs, paths, response bodies, credentials, or exception messages.
type clientDiagnostic struct {
	ID                       string `json:"id"`
	ClientVersion            string `json:"client_version"`
	Platform                 string `json:"platform"`
	Architecture             string `json:"architecture"`
	Stage                    string `json:"stage"`
	Code                     string `json:"code"`
	HTTPStatus               int    `json:"http_status,omitempty"`
	RequestID                string `json:"request_id,omitempty"`
	Exception                string `json:"exception,omitempty"`
	Errno                    int    `json:"errno,omitempty"`
	Winerror                 int    `json:"winerror,omitempty"`
	Elevation                string `json:"elevation,omitempty"`
	Rollback                 string `json:"rollback,omitempty"`
	CodexVersion             string `json:"codex_version,omitempty"`
	Source                   string `json:"source,omitempty"`
	SessionIssue             string `json:"session_issue,omitempty"`
	SessionFailed            int    `json:"session_failed,omitempty"`
	SessionActualProvider    string `json:"session_actual_provider,omitempty"`
	SessionNestedProvider    string `json:"session_nested_provider,omitempty"`
	SessionPersistedProvider string `json:"session_persisted_provider,omitempty"`
	SessionThreadMatch       string `json:"session_thread_match,omitempty"`
}

var clientDiagnosticLock sync.Mutex
var clientDiagnosticID = regexp.MustCompile(`^[a-f0-9]{32}$`)
var clientDiagnosticVersion = regexp.MustCompile(`^[0-9]{1,4}(\.[0-9]{1,4}){1,3}([-+][A-Za-z0-9.]{1,24})?$`)
var clientDiagnosticRequestID = regexp.MustCompile(`^[0-9]{14}[A-Za-z0-9]{8,50}$`)
var clientDiagnosticCodes = strings.Fields(`KEY_FORMAT_INVALID KEY_REJECTED KEY_ACCESS_DENIED KEY_RATE_LIMITED KEY_HTTP_ERROR KEY_NETWORK_ERROR KEY_RESPONSE_INVALID KEY_REDIRECT_BLOCKED KEY_EXPIRED
CODEX_NOT_FOUND CODEX_DISCOVERY_INCOMPLETE CODEX_UNRUNNABLE CODEX_PROBE_TIMEOUT CODEX_LOGIN_UNSUPPORTED CODEX_CONFIG_INCOMPATIBLE CODEX_LOGIN_FAILED CODEX_CREDENTIALS_INVALID CODEX_TIMEOUT CODEX_OVERRIDE_INVALID CODEX_RUNTIME_CACHE_UNSAFE CODEX_RUNTIME_COPY_INVALID
CONFIG_TOML_INVALID CONFIG_LOGIN_POLICY CONFIG_MERGE_UNSUPPORTED CONFIG_RESTORE_FAILED CONFIG_SYMLINK FILESYSTEM_ACCESS_DENIED FILESYSTEM_ERROR HELPER_CACHE_INVALID TLS_BUNDLE_MISSING PACKAGE_CATALOG_INVALID DATA_FORMAT_INVALID SETUP_INTERNAL_ERROR
SESSIONS_BUSY SESSIONS_PATH_UNSAFE SESSIONS_RPC_FAILED SESSIONS_TIMEOUT SESSIONS_CHANGED SESSIONS_INDEX_UNSUPPORTED SESSIONS_JOURNAL_INVALID SESSIONS_CLIENT_UNSUPPORTED SESSIONS_VERIFY_FAILED
DOWNLOAD_MANIFEST_PARSE_FAILED DOWNLOAD_MANIFEST_INVALID DOWNLOAD_CACHE_UNSAFE DOWNLOAD_CHECKSUM_FAILED DOWNLOAD_RANGE_UNSUPPORTED DOWNLOAD_WRITE_FAILED DOWNLOAD_INTERRUPTED DOWNLOAD_TOOL_MISSING DOWNLOAD_HTTP_ERROR SETUP_LOCK_UNAVAILABLE PLATFORM_UNSUPPORTED BOOTSTRAP_FAILED CLIENT_FAILED CLIENT_POLICY_BLOCKED CLIENT_START_FAILED CLIENT_ELEVATION_REQUIRED CLIENT_ACCESS_DENIED CLIENT_SIGNATURE_BLOCKED`)

func ReceiveClientDiagnostic(c *gin.Context) {
	c.Header("Cache-Control", "no-store")
	body, err := io.ReadAll(http.MaxBytesReader(c.Writer, c.Request.Body, 4096))
	if err != nil {
		c.AbortWithStatus(http.StatusRequestEntityTooLarge)
		return
	}
	var report clientDiagnostic
	providerLabels := []string{"", "realyu", "openai", "other", "missing", "invalid"}
	if common.Unmarshal(body, &report) != nil ||
		!clientDiagnosticID.MatchString(report.ID) || !clientDiagnosticVersion.MatchString(report.ClientVersion) ||
		!slices.Contains([]string{"win32", "darwin", "linux", "unix"}, report.Platform) ||
		!slices.Contains([]string{"", "AMD64", "x86_64", "arm64", "aarch64", "x86", "i386", "ARM64"}, report.Architecture) ||
		!slices.Contains([]string{"bootstrap", "discovery", "compatibility", "key_validation", "configuration", "sessions"}, report.Stage) ||
		!slices.Contains(clientDiagnosticCodes, report.Code) ||
		!slices.Contains([]string{"", "provider_mismatch", "thread_id_mismatch", "persistence_mismatch", "rpc_failed", "timeout", "busy", "mixed"}, report.SessionIssue) ||
		(report.SessionFailed < 0 || report.SessionFailed > 1000000) ||
		!slices.Contains(providerLabels, report.SessionActualProvider) ||
		!slices.Contains(providerLabels, report.SessionNestedProvider) ||
		!slices.Contains(providerLabels, report.SessionPersistedProvider) ||
		!slices.Contains([]string{"", "matches", "different", "missing"}, report.SessionThreadMatch) ||
		(report.HTTPStatus != 0 && (report.HTTPStatus < 100 || report.HTTPStatus > 599)) ||
		(report.RequestID != "" && !clientDiagnosticRequestID.MatchString(report.RequestID)) ||
		!slices.Contains([]string{"", "SetupError", "TimeoutExpired", "PermissionError", "OSError", "FileNotFoundError", "FileExistsError", "NotADirectoryError", "IsADirectoryError", "TOMLDecodeError", "ValueError", "UnicodeError", "UnicodeDecodeError", "JSONDecodeError", "RuntimeError", "TypeError", "KeyError", "AttributeError", "Other"}, report.Exception) ||
		report.Errno < 0 || report.Errno > 65535 || report.Winerror < 0 || report.Winerror > 65535 ||
		!slices.Contains([]string{"", "standard", "elevated", "unknown"}, report.Elevation) ||
		!slices.Contains([]string{"", "failed", "restored", "not_needed"}, report.Rollback) ||
		(report.CodexVersion != "" && !clientDiagnosticVersion.MatchString(report.CodexVersion)) ||
		!slices.Contains([]string{"", "store", "desktop", "npm", "path", "explicit"}, report.Source) {
		c.AbortWithStatus(http.StatusBadRequest)
		return
	}
	// Re-marshal the allowlist: unknown fields are discarded, never persisted.
	now := time.Now().UTC()
	line, err := common.Marshal(struct {
		ReceivedAt string `json:"received_at"`
		Origin     string `json:"origin"`
		clientDiagnostic
	}{now.Format(time.RFC3339), "unverified_client", report})
	if err != nil {
		c.AbortWithStatus(http.StatusInternalServerError)
		return
	}
	clientDiagnosticLock.Lock()
	defer clientDiagnosticLock.Unlock()
	folder := filepath.Join(*common.LogDir, "client-diagnostics")
	if info, err := os.Lstat(folder); err == nil && (!info.IsDir() || info.Mode()&os.ModeSymlink != 0) {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	if err := os.MkdirAll(folder, 0700); err != nil {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	// One bounded file per random report ID gives durable retry deduplication.
	path := filepath.Join(folder, report.ID+".json")
	if info, err := os.Lstat(path); err == nil {
		if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
			c.AbortWithStatus(http.StatusServiceUnavailable)
			return
		}
		c.JSON(http.StatusAccepted, gin.H{"success": true, "id": report.ID})
		return
	} else if !os.IsNotExist(err) {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	entries, err := os.ReadDir(folder)
	if err != nil {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	count := 0
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") || !clientDiagnosticID.MatchString(strings.TrimSuffix(entry.Name(), ".json")) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			c.AbortWithStatus(http.StatusServiceUnavailable)
			return
		}
		if info.ModTime().Before(now.AddDate(0, 0, -30)) {
			if os.Remove(filepath.Join(folder, entry.Name())) != nil {
				c.AbortWithStatus(http.StatusServiceUnavailable)
				return
			}
		} else {
			count++
		}
	}
	// 5,000 reports at <= 4 KiB each; never grow an unbounded public log sink.
	if count >= 5000 || len(line) > 4096 {
		c.Header("Retry-After", "3600")
		c.AbortWithStatus(http.StatusTooManyRequests)
		return
	}
	file, err := os.CreateTemp(folder, ".diagnostic-*.tmp")
	if err != nil {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	defer os.Remove(file.Name())
	_, writeErr := file.Write(append(line, '\n'))
	syncErr := file.Sync()
	closeErr := file.Close()
	if writeErr != nil || syncErr != nil || closeErr != nil || os.Rename(file.Name(), path) != nil {
		c.AbortWithStatus(http.StatusServiceUnavailable)
		return
	}
	c.JSON(http.StatusAccepted, gin.H{"success": true, "id": report.ID})
}
