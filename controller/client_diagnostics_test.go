package controller

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestClientDiagnosticCollection(t *testing.T) {
	previous := *common.LogDir
	*common.LogDir = t.TempDir()
	t.Cleanup(func() { *common.LogDir = previous })
	r := gin.New()
	r.POST("/api/client/diagnostics", ReceiveClientDiagnostic)
	const id = "0123456789abcdef0123456789abcdef"
	body := `{"id":"` + id + `","client_version":"1.4.5","platform":"win32","architecture":"AMD64","stage":"key_validation","code":"KEY_REJECTED","http_status":401,"elevation":"standard","winerror":4551,"secret":"sk-never-persist","path":"C:/private/person","exception_message":"secret"}`
	var wg sync.WaitGroup
	codes := make(chan int, 24)
	for range 24 {
		wg.Go(func() {
			w := httptest.NewRecorder()
			r.ServeHTTP(w, httptest.NewRequest("POST", "/api/client/diagnostics", strings.NewReader(body)))
			codes <- w.Code
		})
	}
	wg.Wait()
	close(codes)
	for code := range codes {
		assert.Equal(t, http.StatusAccepted, code)
	}
	folder := filepath.Join(*common.LogDir, "client-diagnostics")
	files, err := os.ReadDir(folder)
	require.NoError(t, err)
	require.Len(t, files, 1, "concurrent retries persist one report")
	stored, err := os.ReadFile(filepath.Join(folder, id+".json"))
	require.NoError(t, err)
	assert.Contains(t, string(stored), `"origin":"unverified_client"`)
	assert.NotContains(t, string(stored), "secret")
	assert.NotContains(t, string(stored), "private")
	assert.NotContains(t, string(stored), "sk-")
	assert.Contains(t, string(stored), `"elevation":"standard"`)
	assert.Contains(t, string(stored), `"winerror":4551`)
	for _, test := range []struct {
		name string
		body string
		code int
	}{
		{"oversized", strings.Repeat("x", 4097), 413},
		{"bad json", "{", 400},
		{"traversal", strings.Replace(body, id, "../../config", 1), 400},
		{"secret in code", strings.Replace(body, "KEY_REJECTED", "sk-secret", 1), 400},
		{"unknown stage", strings.Replace(body, "key_validation", "C:/private", 1), 400},
		{"invalid status", strings.Replace(body, ":401", ":900", 1), 400},
		{"invalid elevation", strings.Replace(body, "standard", "private-windows-account", 1), 400},
		{"elevated policy report", strings.ReplaceAll(strings.Replace(body, "standard", "elevated", 1), "KEY_REJECTED", "CLIENT_POLICY_BLOCKED"), 202},
		{"signature report", strings.Replace(body, "KEY_REJECTED", "CLIENT_SIGNATURE_BLOCKED", 1), 202},
		{"elevation required report", strings.Replace(body, "KEY_REJECTED", "CLIENT_ELEVATION_REQUIRED", 1), 202},
		{"access denied report", strings.Replace(body, "KEY_REJECTED", "CLIENT_ACCESS_DENIED", 1), 202},
		{"session migration report", strings.ReplaceAll(strings.Replace(body, "key_validation", "sessions", 1), "KEY_REJECTED", "SESSIONS_VERIFY_FAILED"), 202},
		{"session writer busy", strings.Replace(body, "KEY_REJECTED", "SESSIONS_BUSY", 1), 202},
		{"unknown session code", strings.Replace(body, "KEY_REJECTED", "SESSIONS_secret", 1), 400},
		{"session reason", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_issue":"provider_mismatch","session_failed":1`, 1), 202},
		{"private session reason", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_issue":"private-thread-title"`, 1), 400},
		{"negative session count", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_failed":-1`, 1), 400},
		{"excessive session count", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_failed":1000001`, 1), 400},
		{"session comparison", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_actual_provider":"openai","session_nested_provider":"openai","session_persisted_provider":"openai","session_thread_match":"matches"`, 1), 202},
		{"private provider value", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_actual_provider":"sk-private-fixture"`, 1), 400},
		{"private returned id", strings.Replace(body, `"secret":"sk-never-persist"`, `"session_thread_match":"private-thread-id"`, 1), 400},
	} {
		t.Run(test.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			r.ServeHTTP(w, httptest.NewRequest("POST", "/api/client/diagnostics", strings.NewReader(test.body)))
			assert.Equal(t, test.code, w.Code)
		})
	}
	old := filepath.Join(folder, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json")
	require.NoError(t, os.WriteFile(old, []byte("{}"), 0600))
	require.NoError(t, os.Chtimes(old, time.Now().AddDate(0, 0, -31), time.Now().AddDate(0, 0, -31)))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest("POST", "/api/client/diagnostics", strings.NewReader(strings.ReplaceAll(body, id, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))))
	assert.Equal(t, 202, w.Code)
	_, err = os.Stat(old)
	assert.True(t, os.IsNotExist(err), "reports expire after 30 days")
	*common.LogDir = filepath.Join(folder, id+".json", "invalid-parent")
	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest("POST", "/api/client/diagnostics", strings.NewReader(body)))
	assert.Equal(t, 503, w.Code, "failed persistence must never acknowledge success")
}

func TestClientDiagnosticStorageBound(t *testing.T) {
	previous := *common.LogDir
	*common.LogDir = t.TempDir()
	t.Cleanup(func() { *common.LogDir = previous })
	folder := filepath.Join(*common.LogDir, "client-diagnostics")
	require.NoError(t, os.Mkdir(folder, 0700))
	for i := range 5000 {
		require.NoError(t, os.WriteFile(filepath.Join(folder, fmt.Sprintf("%032x.json", i)), []byte("{}"), 0600))
	}
	r := gin.New()
	r.POST("/report", ReceiveClientDiagnostic)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest("POST", "/report", strings.NewReader(`{"id":"ffffffffffffffffffffffffffffffff","client_version":"1.4.4","platform":"linux","stage":"bootstrap","code":"BOOTSTRAP_FAILED"}`)))
	assert.Equal(t, 429, w.Code)
	assert.NotEmpty(t, w.Header().Get("Retry-After"))
}

func TestClientInstallIdentityFlow(t *testing.T) {
	setupTeamDatabase(t)
	owner := model.User{Username: "install-owner", Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default", AffCode: "install-owner"}
	require.NoError(t, model.DB.Create(&owner).Error)
	_, err := model.CreateWorkspaceTeam(owner.Id, "Installer test")
	require.NoError(t, err)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	var keys []*model.Token
	for i := range 32 {
		user := model.User{Username: fmt.Sprintf("install-member-%d", i), Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default", AffCode: fmt.Sprintf("install-%d", i)}
		require.NoError(t, model.DB.Create(&user).Error)
		require.NoError(t, model.JoinWorkspaceTeam(user.Id, invite))
		key, err := model.GetWorkspaceKey(user.Id, false)
		require.NoError(t, err)
		keys = append(keys, key)
	}
	r := gin.New()
	require.NoError(t, r.SetTrustedProxies(nil))
	r.GET("/check", middleware.ClientInstallIPRateLimit(), middleware.TokenAuthReadOnly(), middleware.ClientInstallTokenRateLimit(), GetTokenUsage)
	for _, key := range keys {
		w := httptest.NewRecorder()
		req := httptest.NewRequest("GET", "/check", nil)
		req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(key))
		r.ServeHTTP(w, req)
		require.Equal(t, 200, w.Code, "32 coworkers with distinct team keys must not share a 20-request bucket")
		assert.Contains(t, w.Body.String(), `"total_available":0`)
	}
	for i := range 10 {
		w := httptest.NewRecorder()
		req := httptest.NewRequest("GET", "/check", nil)
		req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(keys[0]))
		r.ServeHTTP(w, req)
		if i < 9 {
			assert.Equal(t, 200, w.Code)
		} else {
			assert.Equal(t, 429, w.Code)
			assert.NotEmpty(t, w.Header().Get("Retry-After"))
		}
	}
	w := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/check", nil)
	req.Header.Set("Authorization", "Bearer "+model.WorkspaceKeyText(keys[1]))
	r.ServeHTTP(w, req)
	assert.Equal(t, 200, w.Code, "one key's limit must not block another member")
	// Team keys authenticate independently of their weekly funding. Exercise
	// the real billing gate instead of a mock handler that always returns 200.
	assert.Equal(t, 403, fundingRequest(t, keys[1], "installer-zero-team-funding", 1, 1).Code)
	require.NoError(t, model.DB.First(&owner, owner.Id).Error)
	assert.Zero(t, owner.Quota)
	assert.Zero(t, owner.UsedQuota)
	require.NoError(t, model.DB.Model(keys[2]).Update("status", common.TokenStatusDisabled).Error)
	for _, credential := range []string{model.WorkspaceKeyText(keys[2]), "sk-invalid-fixture-key"} {
		w := httptest.NewRecorder()
		req := httptest.NewRequest("GET", "/check", nil)
		req.Header.Set("Authorization", "Bearer "+credential)
		r.ServeHTTP(w, req)
		assert.Equal(t, 401, w.Code)
	}
}
