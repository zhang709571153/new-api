package service

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/tidwall/gjson"
)

func TestSub2APIObservabilityScopesAndSanitizesNativeData(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "test-admin", r.Header.Get("x-api-key"))
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/admin/groups/10":
			_, _ = w.Write([]byte(`{"code":0,"data":{"id":10,"name":"Supply"}}`))
		case "/api/v1/admin/accounts":
			assert.Equal(t, "10", r.URL.Query().Get("group"))
			_, _ = w.Write([]byte(`{"code":0,"data":{"pages":1,"items":[{"id":3,"name":"Account three","group_ids":[10],"credentials":{"access_token":"secret"},"extra":{"codex_5h_used_percent":0,"private":"secret"}},{"id":4,"group_ids":[20]}]}}`))
		case "/api/v1/admin/accounts/today-stats/batch":
			var body struct {
				IDs []int `json:"account_ids"`
			}
			assert.NoError(t, common.DecodeJson(r.Body, &body))
			assert.Equal(t, []int{3}, body.IDs)
			_, _ = w.Write([]byte(`{"code":0,"data":{"stats":{"3":{"requests":7,"tokens":900,"cost":123}}}}`))
		case "/api/v1/admin/usage":
			switch r.URL.Query().Get("request_id") {
			case "client:exact":
				_, _ = w.Write([]byte(`{"code":0,"data":{"total":1,"items":[{"id":42,"request_id":"client:exact","account_id":3,"group_id":10,"model":"gpt-test","account":{"name":"Account three","credentials":"secret"},"group":{"name":"Supply"},"api_key":{"key":"secret"},"user":{"email":"private"}}]}}`))
			case "client:wrong-group":
				_, _ = w.Write([]byte(`{"code":0,"data":{"total":1,"items":[{"id":43,"request_id":"client:wrong-group","account_id":4,"group_id":20}]}}`))
			case "client:substring":
				_, _ = w.Write([]byte(`{"code":0,"data":{"total":1,"items":[{"id":44,"request_id":"client:substring-more","account_id":3,"group_id":10}]}}`))
			default:
				w.WriteHeader(http.StatusServiceUnavailable)
			}
		default:
			t.Errorf("unexpected native request %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	writeSub2APIConfig(t, server.URL, func(cfg *Sub2APIConfig) { cfg.Pools = cfg.Pools[:1] })
	view, err := GetSub2APIOverview(context.Background())
	require.NoError(t, err)
	require.Len(t, view.Pools, 1)
	require.Len(t, view.Pools[0].Accounts, 1)
	a := view.Pools[0].Accounts[0]
	require.NotNil(t, a.FiveHourPercent)
	assert.Zero(t, *a.FiveHourPercent)
	assert.Nil(t, a.WeeklyPercent)
	require.NotNil(t, a.Today)
	assert.Equal(t, int64(7), a.Today.Requests)
	raw, err := common.Marshal(view)
	require.NoError(t, err)
	for _, forbidden := range []string{"secret", "credentials", "private", "cost"} {
		assert.NotContains(t, string(raw), forbidden)
	}
	logs := []*model.Log{
		{ChannelId: 59, Type: model.LogTypeConsume, UpstreamRequestId: "exact", Other: `{"cost":9007199254740993,"admin_info":{"use_channel":[59]},"root_info":{"debug":1}}`},
		{ChannelId: 59, Type: model.LogTypeConsume, UpstreamRequestId: "wrong-group"},
		{ChannelId: 59, Type: model.LogTypeConsume, UpstreamRequestId: "substring"},
		{ChannelId: 59, Type: model.LogTypeError, UpstreamRequestId: "unavailable"},
		{ChannelId: 1, Type: model.LogTypeConsume, Other: `{"legacy":true}`},
	}
	EnrichSub2APIAdminLogs(context.Background(), logs)
	assert.Equal(t, "Account three", gjson.Get(logs[0].Other, "admin_info.sub2api.account_name").String())
	assert.Contains(t, logs[0].Other, "9007199254740993")
	assert.Equal(t, "not_recorded", gjson.Get(logs[1].Other, "admin_info.sub2api.status").String())
	assert.Equal(t, "not_recorded", gjson.Get(logs[2].Other, "admin_info.sub2api.status").String())
	assert.Equal(t, "unavailable", gjson.Get(logs[3].Other, "admin_info.sub2api.status").String())
	assert.Equal(t, `{"legacy":true}`, logs[4].Other)
	assert.NotContains(t, logs[0].Other, "secret")
	model.FormatAdminLogs(logs)
	assert.True(t, gjson.Get(logs[0].Other, "admin_info.sub2api").Exists())
	assert.False(t, gjson.Get(logs[0].Other, "root_info").Exists())
}

func writeSub2APIConfig(t *testing.T, baseURL string, edit func(*Sub2APIConfig)) string {
	t.Helper()
	cfg := Sub2APIConfig{Version: 1, Namespace: "test-projection", IdentitySecret: strings.Repeat("s", 32), AdminAPIKey: "test-admin", Pools: []Sub2APIPool{{ChannelID: 59, BaseURL: baseURL, GroupID: 10}, {ChannelID: 60, BaseURL: baseURL, GroupID: 20}}}
	cfg.Provision.Enabled, cfg.Provision.InitialBalance, cfg.Provision.Concurrency = true, 100, 10
	cfg.Provision.Mode = "isolated-lazy"
	if edit != nil {
		edit(&cfg)
	}
	raw, err := common.Marshal(cfg)
	require.NoError(t, err)
	path := filepath.Join(t.TempDir(), "sub2api.json")
	require.NoError(t, os.WriteFile(path, raw, 0600))
	t.Setenv("REALYU_SUB2API_BINDINGS_FILE", path)
	t.Setenv("REALYU_UPSTREAM_DRIVER", "sub2api")
	return path
}

// This fixture tests the gateway's management HTTP contract, not the real
// Sub2API binary. Real service/database/upstream acceptance is recorded separately.
type projectionFixture struct {
	sync.Mutex
	users                       map[string]sub2APIProjectedUser
	keys                        map[string]sub2APIProjectedKey
	passwords                   map[string]string
	creates, logins, keyCreates int
	reject                      int
}

func (f *projectionFixture) serve(w http.ResponseWriter, r *http.Request) {
	f.Lock()
	defer f.Unlock()
	if f.reject != 0 {
		w.WriteHeader(f.reject)
		_, _ = w.Write([]byte(`{"message":"upstream sensitive detail and admin-token"}`))
		return
	}
	var input map[string]any
	if r.Body != nil {
		_ = common.DecodeJson(r.Body, &input)
	}
	respond := func(value any) {
		raw, _ := common.Marshal(map[string]any{"code": 0, "data": value})
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(raw)
	}
	if strings.HasPrefix(r.URL.Path, "/api/v1/admin/") && r.Header.Get("x-api-key") != "test-admin" {
		w.WriteHeader(401)
		return
	}
	switch {
	case strings.HasPrefix(r.URL.Path, "/api/v1/admin/groups/"):
		group, _ := strconv.Atoi(strings.TrimPrefix(r.URL.Path, "/api/v1/admin/groups/"))
		respond(map[string]any{"id": group, "platform": "openai", "status": "active", "subscription_type": "standard"})
	case r.URL.Path == "/api/v1/admin/users" && r.Method == http.MethodGet:
		items := []sub2APIProjectedUser{}
		if user, ok := f.users[r.URL.Query().Get("search")]; ok {
			items = append(items, user)
		}
		respond(map[string]any{"items": items, "pages": 1})
	case r.URL.Path == "/api/v1/admin/users" && r.Method == http.MethodPost:
		email, _ := input["email"].(string)
		if _, ok := f.users[email]; ok {
			w.WriteHeader(409)
			return
		}
		user := sub2APIProjectedUser{ID: len(f.users) + 1, Email: email, Notes: input["notes"].(string), Role: input["role"].(string), Status: "active", RestrictPublicGroups: true}
		for _, id := range input["allowed_groups"].([]any) {
			user.AllowedGroups = append(user.AllowedGroups, int(id.(float64)))
		}
		f.users[email] = user
		f.passwords[email] = input["password"].(string)
		f.creates++
		respond(user)
	case strings.HasPrefix(r.URL.Path, "/api/v1/admin/users/") && r.Method == http.MethodPut:
		id, _ := strconv.Atoi(strings.TrimPrefix(r.URL.Path, "/api/v1/admin/users/"))
		for email, user := range f.users {
			if user.ID == id {
				user.AllowedGroups = nil
				for _, group := range input["allowed_groups"].([]any) {
					user.AllowedGroups = append(user.AllowedGroups, int(group.(float64)))
				}
				user.RestrictPublicGroups = input["restrict_public_groups"].(bool)
				f.users[email] = user
			}
		}
		respond(nil)
	case strings.HasSuffix(r.URL.Path, "/api-keys"):
		parts := strings.Split(r.URL.Path, "/")
		userID, _ := strconv.Atoi(parts[len(parts)-2])
		items := []sub2APIProjectedKey{}
		for _, key := range f.keys {
			if key.UserID == userID {
				items = append(items, key)
			}
		}
		respond(map[string]any{"items": items, "pages": 1})
	case r.URL.Path == "/api/v1/auth/login":
		email, _ := input["email"].(string)
		if f.passwords[email] != input["password"] {
			w.WriteHeader(401)
			return
		}
		f.logins++
		respond(map[string]any{"access_token": fmt.Sprintf("user-%d", f.users[email].ID)})
	case r.URL.Path == "/api/v1/keys":
		userID, _ := strconv.Atoi(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer user-"))
		if userID == 0 {
			w.WriteHeader(401)
			return
		}
		key := input["custom_key"].(string)
		groupID := int(input["group_id"].(float64))
		allowed := false
		for _, user := range f.users {
			if user.ID == userID {
				for _, group := range user.AllowedGroups {
					if group == groupID {
						allowed = true
					}
				}
			}
		}
		if !allowed {
			w.WriteHeader(403)
			return
		}
		if _, ok := f.keys[key]; ok {
			w.WriteHeader(409)
			return
		}
		f.keys[key] = sub2APIProjectedKey{UserID: userID, GroupID: int(input["group_id"].(float64)), Key: key, Status: "active"}
		f.keyCreates++
		respond(f.keys[key])
	default:
		w.WriteHeader(404)
	}
}

func newProjectionFixture(t *testing.T) (*projectionFixture, *httptest.Server) {
	t.Helper()
	f := &projectionFixture{users: map[string]sub2APIProjectedUser{}, keys: map[string]sub2APIProjectedKey{}, passwords: map[string]string{}}
	s := httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(s.Close)
	return f, s
}

func TestSub2APIProjectionIsolatesCustomersPoolsAndRecoversAfterRestart(t *testing.T) {
	f, server := newProjectionFixture(t)
	writeSub2APIConfig(t, server.URL, nil)
	ctx := context.Background()
	var wg sync.WaitGroup
	results := make(chan Sub2APIBinding, 8)
	errors := make(chan error, 8)
	for i := range 8 {
		wg.Go(func() {
			channel := 59 + i%2
			b, err := ResolveSub2APIBinding(ctx, Sub2APISubject{UserID: 1}, channel, server.URL)
			if err != nil {
				errors <- err
				return
			}
			results <- b
		})
	}
	wg.Wait()
	close(results)
	close(errors)
	for err := range errors {
		require.NoError(t, err)
	}
	byPool := map[int]Sub2APIBinding{}
	for b := range results {
		assert.Equal(t, 1, b.UserID)
		byPool[b.ChannelID] = b
	}
	require.Len(t, byPool, 2)
	assert.Equal(t, byPool[59].UpstreamUserID, byPool[60].UpstreamUserID)
	assert.NotEqual(t, byPool[59].APIKey, byPool[60].APIKey)
	assert.Equal(t, 10, byPool[59].GroupID)
	assert.Equal(t, 20, byPool[60].GroupID)
	other, err := ResolveSub2APIBinding(ctx, Sub2APISubject{UserID: 2}, 59, server.URL)
	require.NoError(t, err)
	assert.NotEqual(t, byPool[59].UpstreamUserID, other.UpstreamUserID)
	assert.NotEqual(t, byPool[59].APIKey, other.APIKey)
	f.Lock()
	assert.Equal(t, 2, f.creates)
	assert.Equal(t, 3, f.keyCreates)
	before := f.logins
	f.Unlock()
	sub2APIProjectionCache.Range(func(key, value any) bool { sub2APIProjectionCache.Delete(key); return true })
	recovered, err := ResolveSub2APIBinding(ctx, Sub2APISubject{UserID: 1}, 59, server.URL)
	require.NoError(t, err)
	assert.Equal(t, byPool[59], recovered)
	f.Lock()
	assert.Equal(t, before, f.logins)
	assert.Equal(t, 2, f.creates)
	f.Unlock()
}

func TestSub2APISubjectSeparatesTeamMembersAndPersonalWorkspaces(t *testing.T) {
	f, server := newProjectionFixture(t)
	writeSub2APIConfig(t, server.URL, nil)
	subjects := []Sub2APISubject{{UserID: 71, WorkspaceTeamID: 9}, {UserID: 72, WorkspaceTeamID: 9}, {UserID: 71, WorkspaceTeamID: 10}, {UserID: 71}}
	bindings := make([]Sub2APIBinding, len(subjects))
	sessions, metadata := map[string]bool{}, map[string]bool{}
	for i, expected := range subjects {
		c, _ := gin.CreateTestContext(httptest.NewRecorder())
		c.Set("id", 1) // All team members share this billing owner.
		if expected.WorkspaceTeamID == 0 {
			c.Set("id", expected.UserID)
		} else {
			c.Set("workspace_user_id", expected.UserID)
			c.Set("workspace_team_id", expected.WorkspaceTeamID)
		}
		subject := Sub2APISubjectFromContext(c)
		require.Equal(t, expected, subject)
		var err error
		bindings[i], err = ResolveSub2APIBinding(context.Background(), subject, 59, server.URL)
		require.NoError(t, err)
		require.Equal(t, subject.UserID, bindings[i].UserID)
		require.Equal(t, subject.WorkspaceTeamID, bindings[i].WorkspaceTeamID)
		scoped, err := Sub2APIScopeRequestBody(subject, []byte(`{"prompt_cache_key":"shared","conversation_id":"shared"}`))
		require.NoError(t, err)
		session, err := Sub2APISessionID(subject, "shared")
		require.NoError(t, err)
		assert.Equal(t, session, gjson.GetBytes(scoped, "prompt_cache_key").String())
		assert.Equal(t, session, gjson.GetBytes(scoped, "conversation_id").String())
		sessions[session] = true
		parent, err := Sub2APIScopeTurnMetadata(subject, `{"parent_thread_id":"shared","subagent_kind":"review"}`)
		require.NoError(t, err)
		assert.Equal(t, session, gjson.Get(parent, "parent_thread_id").String())
		metadata[parent] = true
		if expected.WorkspaceTeamID > 0 {
			assert.Equal(t, 1, c.GetInt("id"), "scope must not change the payer")
		}
	}
	upstreamUsers, keys := map[int]bool{}, map[string]bool{}
	for i, binding := range bindings {
		upstreamUsers[binding.UpstreamUserID], keys[binding.APIKey] = true, true
		replayed, err := ResolveSub2APIBinding(context.Background(), subjects[i], 59, server.URL)
		require.NoError(t, err)
		assert.Equal(t, binding, replayed, "cache must use the complete subject")
	}
	assert.Len(t, upstreamUsers, len(subjects))
	assert.Len(t, keys, len(subjects))
	assert.Len(t, sessions, len(subjects))
	assert.Len(t, metadata, len(subjects))
	sub2APIProjectionCache.Clear()
	for i, subject := range subjects {
		recovered, err := ResolveSub2APIBinding(context.Background(), subject, 59, server.URL)
		require.NoError(t, err)
		assert.Equal(t, bindings[i], recovered)
	}
	f.Lock()
	assert.Equal(t, len(subjects), f.creates)
	assert.Equal(t, len(subjects), f.logins, "restart must recover without more logins")
	f.Unlock()
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Set("id", 1)
	c.Set("workspace_team_id", 9)
	_, err := ResolveSub2APIBinding(context.Background(), Sub2APISubjectFromContext(c), 59, server.URL)
	require.ErrorContains(t, err, "authenticated")
}

func TestSub2APIPrewarmedWebOnlyRecoversExistingIdentities(t *testing.T) {
	f, server := newProjectionFixture(t)
	writeSub2APIConfig(t, server.URL, func(cfg *Sub2APIConfig) { cfg.Provision.Mode = "" })
	cfg, err := LoadSub2APIConfig()
	require.NoError(t, err)
	require.Equal(t, "prewarmed", cfg.Provision.Mode)
	subject := Sub2APISubject{UserID: 71, WorkspaceTeamID: 9}
	_, err = ResolveSub2APIBinding(context.Background(), subject, 59, server.URL)
	require.ErrorIs(t, err, errSub2APIPrewarmRequired)
	f.Lock()
	assert.Zero(t, f.creates)
	assert.Zero(t, f.logins)
	assert.Zero(t, f.keyCreates)
	f.Unlock()
	err = PrewarmSub2APIIdentity(context.Background(), subject, 59, "different-generation")
	require.ErrorContains(t, err, "generation")
	require.NoError(t, PrewarmSub2APIIdentity(context.Background(), subject, 59, cfg.digest))
	binding, err := ResolveSub2APIBinding(context.Background(), subject, 59, server.URL)
	require.NoError(t, err)
	sub2APIProjectionCache.Clear()
	recovered, err := ResolveSub2APIBinding(context.Background(), subject, 59, server.URL)
	require.NoError(t, err)
	assert.Equal(t, binding, recovered)
	f.Lock()
	assert.Equal(t, 1, f.creates)
	assert.Equal(t, 1, f.logins)
	assert.Equal(t, 1, f.keyCreates)
	delete(f.keys, binding.APIKey)
	f.Unlock()
	sub2APIProjectionCache.Clear()
	_, err = ResolveSub2APIBinding(context.Background(), subject, 59, server.URL)
	require.ErrorIs(t, err, errSub2APIPrewarmRequired)
	f.Lock()
	assert.Equal(t, 1, f.logins, "web must not log in to recreate a removed key")
	assert.Equal(t, 1, f.keyCreates)
	f.Unlock()
	for _, status := range []int{400, 401, 403, 423} {
		state, code := Sub2APIPrewarmFailure(&Sub2APIManagementError{Status: status})
		assert.Equal(t, "BLOCKED", state)
		assert.Equal(t, status, code)
	}
	state, _ := Sub2APIPrewarmFailure(errSub2APIInteractive)
	assert.Equal(t, "BLOCKED", state)
	assert.Equal(t, 75*time.Second, Sub2APIPrewarmRetryDelay(&Sub2APIManagementError{Status: 429, RetryAfter: 75 * time.Second}))
	assert.Equal(t, time.Minute, Sub2APIPrewarmRetryDelay(&Sub2APIManagementError{Status: 429, RetryAfter: time.Second}))
	writeSub2APIConfig(t, "https://supplier.example", nil)
	_, err = LoadSub2APIConfig()
	require.ErrorContains(t, err, "loopback")
}

func TestSub2APIProjectionConcurrentProcessesUseUniqueIdentity(t *testing.T) {
	f, server := newProjectionFixture(t)
	writeSub2APIConfig(t, server.URL, nil)
	cfg, err := LoadSub2APIConfig()
	require.NoError(t, err)
	var wg sync.WaitGroup
	results := make(chan Sub2APIBinding, 2)
	errors := make(chan error, 2)
	for range 2 {
		wg.Go(func() {
			b, err := provisionSub2APIUser(context.Background(), cfg, cfg.Pools[0], Sub2APISubject{UserID: 7})
			if err != nil {
				errors <- err
				return
			}
			results <- b
		})
	}
	wg.Wait()
	close(results)
	close(errors)
	for err := range errors {
		require.NoError(t, err)
	}
	var first Sub2APIBinding
	for b := range results {
		if first.APIKey == "" {
			first = b
		} else {
			assert.Equal(t, first, b)
		}
	}
	f.Lock()
	assert.Equal(t, 1, f.creates)
	assert.Equal(t, 1, f.keyCreates)
	f.Unlock()
}

func TestSub2APIExistingCustomerGainsNewPoolsAcrossProcesses(t *testing.T) {
	f, server := newProjectionFixture(t)
	writeSub2APIConfig(t, server.URL, func(cfg *Sub2APIConfig) { cfg.Pools = cfg.Pools[:1] })
	first, err := ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 1}, 59, server.URL)
	require.NoError(t, err)
	writeSub2APIConfig(t, server.URL, func(cfg *Sub2APIConfig) {
		cfg.Pools = append(cfg.Pools, Sub2APIPool{ChannelID: 61, BaseURL: server.URL, GroupID: 30})
	})
	cfg, err := LoadSub2APIConfig()
	require.NoError(t, err)
	var wg sync.WaitGroup
	errors := make(chan error, 2)
	for _, pool := range cfg.Pools[1:] {
		wg.Go(func() {
			_, err := provisionSub2APIUser(context.Background(), cfg, pool, Sub2APISubject{UserID: 1})
			errors <- err
		})
	}
	wg.Wait()
	close(errors)
	for err := range errors {
		require.NoError(t, err)
	}
	f.Lock()
	defer f.Unlock()
	assert.Equal(t, 1, f.creates)
	for _, user := range f.users {
		assert.Equal(t, first.UpstreamUserID, user.ID)
		assert.ElementsMatch(t, []int{10, 20, 30}, user.AllowedGroups)
		assert.True(t, user.RestrictPublicGroups)
	}
	assert.Equal(t, 3, f.keyCreates)
}

func TestSub2APIProjectionFailsClosedWithoutLeakingUpstreamErrors(t *testing.T) {
	for _, status := range []int{423, 429, 503} {
		t.Run(strconv.Itoa(status), func(t *testing.T) {
			f, server := newProjectionFixture(t)
			f.reject = status
			writeSub2APIConfig(t, server.URL, nil)
			_, err := ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 1}, 59, server.URL)
			require.Error(t, err)
			assert.Contains(t, err.Error(), strconv.Itoa(status))
			assert.NotContains(t, err.Error(), "admin-token")
			assert.NotContains(t, err.Error(), "sensitive detail")
			f.Lock()
			assert.Zero(t, f.creates)
			assert.Zero(t, f.logins)
			f.Unlock()
		})
	}
	f, server := newProjectionFixture(t)
	path := writeSub2APIConfig(t, server.URL, nil)
	b, err := ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 1}, 59, server.URL)
	require.NoError(t, err)
	f.Lock()
	key := f.keys[b.APIKey]
	key.GroupID = 999
	f.keys[b.APIKey] = key
	f.Unlock()
	sub2APIProjectionCache.Range(func(key, value any) bool { sub2APIProjectionCache.Delete(key); return true })
	_, err = ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 1}, 59, server.URL)
	require.ErrorContains(t, err, "pool")
	require.NoError(t, os.WriteFile(path, []byte("invalid config"), 0600))
	_, err = ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 1}, 59, server.URL)
	require.Error(t, err)
}

func TestSub2APIBindingsRejectSharedIdentityAndUnexpectedDestination(t *testing.T) {
	writeSub2APIConfig(t, "http://127.0.0.1:9999", func(cfg *Sub2APIConfig) {
		cfg.Bindings = []Sub2APIBinding{
			{UserID: 1, UpstreamUserID: 3, ChannelID: 59, GroupID: 10, BaseURL: cfg.Pools[0].BaseURL, APIKey: "key-a"},
			{UserID: 2, UpstreamUserID: 3, ChannelID: 59, GroupID: 10, BaseURL: cfg.Pools[0].BaseURL, APIKey: "key-b"},
		}
	})
	_, err := LoadSub2APIConfig()
	require.ErrorContains(t, err, "share")
	writeSub2APIConfig(t, "http://127.0.0.1:9999", func(cfg *Sub2APIConfig) {
		cfg.Bindings = []Sub2APIBinding{
			{UserID: 1, UpstreamUserID: 3, ChannelID: 59, GroupID: 10, BaseURL: cfg.Pools[0].BaseURL, APIKey: "personal"},
			{UserID: 1, WorkspaceTeamID: 9, UpstreamUserID: 3, ChannelID: 59, GroupID: 10, BaseURL: cfg.Pools[0].BaseURL, APIKey: "team"},
		}
	})
	_, err = LoadSub2APIConfig()
	require.ErrorContains(t, err, "share", "a member's personal and team bindings must not share ownership")
	writeSub2APIConfig(t, "http://127.0.0.1:9999", nil)
	_, err = ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 1}, 59, "http://unexpected.invalid")
	require.ErrorContains(t, err, "route")
	_, err = ResolveSub2APIBinding(context.Background(), Sub2APISubject{UserID: 0}, 59, "http://127.0.0.1:9999")
	require.ErrorContains(t, err, "authenticated")
}

func TestSub2APISessionScopePreservesProtocolAndSeparatesUsers(t *testing.T) {
	writeSub2APIConfig(t, "http://127.0.0.1:9999", nil)
	body := []byte(`{"prompt_cache_key":"same-session","store":false,"temperature":0,"tools":[{"type":"custom","format":{"type":"grammar","definition":"x"}}],"input":[{"type":"reasoning","encrypted_content":"opaque"}],"future":{"flag":false}}`)
	a, err := Sub2APIScopeRequestBody(Sub2APISubject{UserID: 1}, body)
	require.NoError(t, err)
	b, err := Sub2APIScopeRequestBody(Sub2APISubject{UserID: 2}, body)
	require.NoError(t, err)
	assert.NotEqual(t, gjson.GetBytes(a, "prompt_cache_key").String(), gjson.GetBytes(b, "prompt_cache_key").String())
	assert.Equal(t, gjson.GetBytes(body, "input").Raw, gjson.GetBytes(a, "input").Raw)
	assert.Equal(t, gjson.GetBytes(body, "tools").Raw, gjson.GetBytes(a, "tools").Raw)
	assert.Equal(t, "false", gjson.GetBytes(a, "store").Raw)
	assert.Equal(t, "0", gjson.GetBytes(a, "temperature").Raw)
	assert.Equal(t, gjson.GetBytes(body, "future").Raw, gjson.GetBytes(a, "future").Raw)
	again, err := Sub2APIScopeRequestBody(Sub2APISubject{UserID: 1}, body)
	require.NoError(t, err)
	assert.Equal(t, a, again)
	parent, err := Sub2APISessionID(Sub2APISubject{UserID: 1}, "parent-session")
	require.NoError(t, err)
	metadata := `{"parent_thread_id":"parent-session","subagent_kind":"review","future":false}`
	scoped, err := Sub2APIScopeTurnMetadata(Sub2APISubject{UserID: 1}, metadata)
	require.NoError(t, err)
	assert.Equal(t, parent, gjson.Get(scoped, "parent_thread_id").String())
	assert.Equal(t, "review", gjson.Get(scoped, "subagent_kind").String())
	assert.Equal(t, "false", gjson.Get(scoped, "future").Raw)
	withMetadata, err := common.Marshal(map[string]any{"client_metadata": map[string]string{"x-codex-turn-metadata": metadata}})
	require.NoError(t, err)
	withMetadata, err = Sub2APIScopeRequestBody(Sub2APISubject{UserID: 1}, withMetadata)
	require.NoError(t, err)
	assert.Equal(t, scoped, gjson.GetBytes(withMetadata, "client_metadata.x-codex-turn-metadata").String())
}

func TestSub2APICodexCatalogFiltersDescriptorsWithoutLosingFields(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer tenant-key", r.Header.Get("Authorization"))
		assert.Equal(t, "0.160.0", r.URL.Query().Get("client_version"))
		_, _ = w.Write([]byte(`{"models":[{"slug":"allowed","input_modalities":["text","image"],"supports_image_detail_original":true,"future":3},{"slug":"private"}]}`))
	}))
	defer server.Close()
	models, err := FetchSub2APICodexModels(context.Background(), Sub2APIBinding{BaseURL: server.URL, APIKey: "tenant-key"}, "0.160.0", map[string]bool{"allowed": true})
	require.NoError(t, err)
	require.Len(t, models, 1)
	assert.Equal(t, "allowed", gjson.GetBytes(models[0], "slug").String())
	assert.True(t, gjson.GetBytes(models[0], "supports_image_detail_original").Bool())
	assert.Equal(t, int64(3), gjson.GetBytes(models[0], "future").Int())
}
