package main

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func prepareWorker(t *testing.T) (worker, string, string, *time.Time, *[]time.Time) {
	t.Helper()
	dir := t.TempDir()
	configPath, queuePath, statePath := filepath.Join(dir, "config.json"), filepath.Join(dir, "queue.json"), filepath.Join(dir, "state.json")
	require.NoError(t, writeAtomicJSON(configPath, map[string]any{"version": 1, "namespace": "worker-test", "identity_secret": strings.Repeat("s", 32), "admin_api_key": "test-admin", "pools": []map[string]any{{"channel_id": 59, "base_url": "http://127.0.0.1:9999", "group_id": 10}}, "provision": map[string]any{"enabled": true, "mode": "prewarmed", "initial_balance": 100, "concurrency": 10}}))
	t.Setenv("REALYU_SUB2API_BINDINGS_FILE", configPath)
	require.NoError(t, writeAtomicJSON(queuePath, queue{Version: 1, Tasks: []task{{UserID: 1, TeamID: 9, ChannelID: 59}, {UserID: 2, TeamID: 9, ChannelID: 59}}}))
	now := time.Date(2026, 10, 9, 1, 0, 0, 0, time.UTC)
	calls := []time.Time{}
	w := worker{Now: func() time.Time { return now }, Sleep: func(_ context.Context, d time.Duration) error { now = now.Add(d); return nil }, Provision: func(_ context.Context, subject service.Sub2APISubject, channel int, digest string) error {
		require.Positive(t, subject.UserID)
		require.Equal(t, 9, subject.WorkspaceTeamID)
		require.Equal(t, 59, channel)
		require.Len(t, digest, 64)
		calls = append(calls, now)
		return nil
	}}
	return w, queuePath, statePath, &now, &calls
}

func TestWorkerPersistsSerialProgressAndResumesWithoutWork(t *testing.T) {
	w, queuePath, statePath, _, calls := prepareWorker(t)
	status, err := w.run(context.Background(), queuePath, statePath, false)
	require.NoError(t, err)
	assert.Equal(t, "DONE", status)
	require.Len(t, *calls, 2)
	assert.GreaterOrEqual(t, (*calls)[1].Sub((*calls)[0]), 4*time.Second)
	status, err = w.run(context.Background(), queuePath, statePath, false)
	require.NoError(t, err)
	assert.Equal(t, "DONE", status)
	assert.Len(t, *calls, 2)
	var saved state
	raw, err := readPrivateJSON(statePath, &saved)
	require.NoError(t, err)
	assert.NotContains(t, string(raw), "test-admin")
	assert.NotContains(t, string(raw), "identity_secret")
	assert.Equal(t, "DONE", saved.Tasks["1:9:59"].Status)
	assert.Equal(t, 1, saved.Tasks["1:9:59"].Attempts)
}

func TestWorkerPersists429DeadlineAndStopsForOperatorBlocks(t *testing.T) {
	for _, code := range []int{429, 423, 400} {
		t.Run(time.Duration(code).String(), func(t *testing.T) {
			w, queuePath, statePath, now, calls := prepareWorker(t)
			provision := w.Provision
			w.Provision = func(ctx context.Context, s service.Sub2APISubject, c int, d string) error {
				_ = provision(ctx, s, c, d)
				return &service.Sub2APIManagementError{Status: code}
			}
			status, err := w.run(context.Background(), queuePath, statePath, false)
			require.Error(t, err)
			require.Len(t, *calls, 1)
			var saved state
			_, err = readPrivateJSON(statePath, &saved)
			require.NoError(t, err)
			if code == 429 {
				assert.Equal(t, "RETRY_WAIT", status)
				assert.GreaterOrEqual(t, saved.NotBefore.Sub(*now), time.Minute)
			} else {
				assert.Equal(t, "BLOCKED", status)
			}
			_, err = w.run(context.Background(), queuePath, statePath, false)
			require.Error(t, err)
			assert.Len(t, *calls, 1, "restart may not bypass retry deadline or operator block")
			*now = now.Add(time.Minute)
			w.Provision = provision
			status, err = w.run(context.Background(), queuePath, statePath, true)
			require.NoError(t, err)
			assert.Equal(t, "DONE", status)
			assert.Len(t, *calls, 3)
		})
	}
}

func TestWorkerRejectsAliasedInputsAndUsesOSLock(t *testing.T) {
	w, queuePath, statePath, _, calls := prepareWorker(t)
	configPath := os.Getenv("REALYU_SUB2API_BINDINGS_FILE")
	alias := filepath.Join(filepath.Dir(configPath), "alias.json")
	require.NoError(t, os.Link(configPath, alias))
	status, err := w.run(context.Background(), queuePath, alias, false)
	require.Error(t, err)
	assert.Equal(t, "INVALID_INPUT", status)
	if runtime.GOOS == "windows" {
		status, err = w.run(context.Background(), queuePath, strings.ToUpper(configPath), false)
		require.Error(t, err)
		assert.Equal(t, "INVALID_INPUT", status)
	}
	lock, err := lockFile(configPath + ".prewarm.lock")
	require.NoError(t, err)
	status, err = w.run(context.Background(), queuePath, statePath, false)
	require.Error(t, err)
	assert.Equal(t, "LOCKED", status)
	assert.Empty(t, *calls)
	require.NoError(t, lock.Close())
	status, err = w.run(context.Background(), queuePath, statePath, false)
	require.NoError(t, err)
	assert.Equal(t, "DONE", status)
}
