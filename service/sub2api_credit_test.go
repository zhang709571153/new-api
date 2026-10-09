package service

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSub2APICreditValidatesManagedIdentityAndUncertainOutcome(t *testing.T) {
	f := &projectionFixture{users: map[string]sub2APIProjectedUser{}, keys: map[string]sub2APIProjectedKey{}, passwords: map[string]string{}}
	var email string
	posts, adds, reject, loseReply := 0, 0, 0, false
	replayed := map[string]sub2APIProjectedUser{}
	bodies := map[string]string{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/balance") {
			f.serve(w, r)
			return
		}
		f.Lock()
		defer f.Unlock()
		posts++
		assert.Equal(t, "/api/v1/admin/users/101/balance", r.URL.Path)
		assert.Equal(t, "test-admin", r.Header.Get("x-api-key"))
		assert.Empty(t, r.Header.Get("Authorization"))
		if reject != 0 {
			w.WriteHeader(reject)
			_, _ = w.Write([]byte(`{"message":"sensitive-upstream-error"}`))
			return
		}
		var input map[string]any
		assert.NoError(t, common.DecodeJson(r.Body, &input))
		key := r.Header.Get("Idempotency-Key")
		assert.NotEmpty(t, key)
		assert.Equal(t, "add", input["operation"])
		assert.Equal(t, float64(100), input["balance"])
		assert.Equal(t, "realyu-internal-credit:test-projection:"+key, input["notes"])
		raw, _ := common.Marshal(input)
		updated, exists := replayed[key]
		if exists {
			assert.Equal(t, bodies[key], string(raw), "manual replay must use the identical operation")
		} else {
			updated = f.users[email]
			balance := *updated.Balance + input["balance"].(float64)
			updated.Balance = &balance
			f.users[email], replayed[key], bodies[key] = updated, updated, string(raw)
			adds++
		}
		if loseReply {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		response, _ := common.Marshal(map[string]any{"code": 0, "data": updated})
		_, _ = w.Write(response)
	}))
	defer server.Close()
	writeSub2APIConfig(t, server.URL, func(cfg *Sub2APIConfig) {
		cfg.Provision.Mode = "prewarmed"
		cfg.Credit = Sub2APICreditConfig{Enabled: true, LowWatermark: 10, TopupAmount: 100, CheckIntervalSeconds: 60}
	})
	cfg, err := LoadSub2APIConfig()
	require.NoError(t, err)
	assert.Equal(t, 3600, cfg.Credit.IdempotencyWindowSeconds)
	subject := Sub2APISubject{UserID: 71, WorkspaceTeamID: 8}
	email, notes := sub2APICreditIdentity(cfg, subject)
	balance := 2.0
	f.users[email] = sub2APIProjectedUser{ID: 101, Email: email, Notes: notes, Role: "user", Status: "active", AllowedGroups: []int{10}, RestrictPublicGroups: true, Balance: &balance}
	key := "sk-" + sub2APIIdentity(cfg, "key:59", subject, 10)
	f.keys[key] = sub2APIProjectedKey{UserID: 101, GroupID: 10, Key: key, Status: "active"}
	ctx := context.Background()
	account, err := InspectSub2APICredit(ctx, subject, 59, cfg.digest)
	require.NoError(t, err)
	assert.Equal(t, 101, account.UpstreamUserID)
	assert.Equal(t, float64(2), account.Balance)
	assert.Zero(t, posts)

	// The mock commits the credit before sending a failure. The service must
	// expose uncertainty rather than guessing the mutation failed or retrying.
	loseReply = true
	_, err = AddSub2APICredit(ctx, subject, 59, cfg.digest, 101, "credit-operation-0001")
	require.Error(t, err)
	assert.True(t, Sub2APICreditOutcomeUncertain(err))
	assert.Equal(t, 1, posts)
	assert.Equal(t, 1, adds)
	loseReply = false
	account, err = AddSub2APICredit(ctx, subject, 59, cfg.digest, 101, "credit-operation-0001")
	require.NoError(t, err)
	assert.Equal(t, float64(102), account.Balance)
	assert.Equal(t, 1, adds)
	assert.Equal(t, 2, posts)

	reject = http.StatusLocked
	_, err = AddSub2APICredit(ctx, subject, 59, cfg.digest, 101, "credit-operation-0002")
	require.Error(t, err)
	assert.False(t, Sub2APICreditOutcomeUncertain(err))
	assert.NotContains(t, err.Error(), "sensitive")
	assert.Equal(t, 1, adds)

	for _, change := range []struct {
		name string
		edit func(*sub2APIProjectedUser)
	}{
		{"foreign namespace", func(u *sub2APIProjectedUser) { u.Notes = "another-operator" }},
		{"disabled user", func(u *sub2APIProjectedUser) { u.Status = "disabled" }},
		{"admin user", func(u *sub2APIProjectedUser) { u.Role = "admin" }},
		{"missing group", func(u *sub2APIProjectedUser) { u.AllowedGroups = []int{20} }},
		{"unrestricted identity", func(u *sub2APIProjectedUser) { u.RestrictPublicGroups = false }},
		{"missing balance", func(u *sub2APIProjectedUser) { u.Balance = nil }},
	} {
		t.Run(change.name, func(t *testing.T) {
			original := f.users[email]
			changed := original
			change.edit(&changed)
			f.users[email] = changed
			before := posts
			_, err := AddSub2APICredit(ctx, subject, 59, cfg.digest, 101, "credit-operation-0003")
			require.Error(t, err)
			assert.False(t, Sub2APICreditOutcomeUncertain(err))
			assert.Equal(t, before, posts)
			f.users[email] = original
		})
	}
	before := posts
	_, err = AddSub2APICredit(ctx, subject, 59, "changed-config", 101, "credit-operation-0003")
	require.Error(t, err)
	_, err = AddSub2APICredit(ctx, subject, 59, cfg.digest, 102, "credit-operation-0003")
	require.Error(t, err)
	_, err = AddSub2APICredit(ctx, Sub2APISubject{UserID: subject.UserID}, 59, cfg.digest, 101, "credit-operation-0003")
	require.Error(t, err)
	assert.Equal(t, before, posts)
	assert.Zero(t, f.creates)
	assert.Zero(t, f.logins)
	assert.Zero(t, f.keyCreates)
}

func TestSub2APICreditRejectsUnsafeConfiguration(t *testing.T) {
	for _, tc := range []struct {
		name string
		edit func(*Sub2APIConfig)
	}{
		{"lazy mode", func(c *Sub2APIConfig) { c.Provision.Mode = "isolated-lazy" }},
		{"zero watermark", func(c *Sub2APIConfig) { c.Credit.LowWatermark = 0 }},
		{"negative amount", func(c *Sub2APIConfig) { c.Credit.TopupAmount = -1 }},
		{"oversized amount", func(c *Sub2APIConfig) { c.Credit.TopupAmount = 1_000_001 }},
		{"insufficient refill", func(c *Sub2APIConfig) { c.Credit.TopupAmount = c.Credit.LowWatermark }},
		{"busy loop", func(c *Sub2APIConfig) { c.Credit.CheckIntervalSeconds = 1 }},
		{"unsafe recovery window", func(c *Sub2APIConfig) { c.Credit.IdempotencyWindowSeconds = 86400 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			writeSub2APIConfig(t, "http://127.0.0.1:29999", func(c *Sub2APIConfig) {
				c.Provision.Mode = "prewarmed"
				c.Credit = Sub2APICreditConfig{Enabled: true, LowWatermark: 10, TopupAmount: 100, CheckIntervalSeconds: 60}
				tc.edit(c)
			})
			_, err := LoadSub2APIConfig()
			require.Error(t, err)
		})
	}
}
