package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func prepareWatch(t *testing.T) (worker, string, string, string, *time.Time, *[]time.Time) {
	t.Helper()
	w, _, _, now, calls := prepareWorker(t)
	base := filepath.Dir(os.Getenv("REALYU_SUB2API_BINDINGS_FILE"))
	queueDir, stateDir := filepath.Join(base, "jobs"), filepath.Join(base, "progress")
	require.NoError(t, os.Mkdir(queueDir, 0700))
	require.NoError(t, os.Mkdir(stateDir, 0700))
	t.Setenv("REALYU_SUB2API_QUEUE_DIR", queueDir)
	t.Setenv("REALYU_SUB2API_STATE_DIR", stateDir)
	raw, err := readPrivateJSON(os.Getenv("REALYU_SUB2API_BINDINGS_FILE"), nil)
	require.NoError(t, err)
	digest := fingerprint(raw)
	require.NoError(t, os.Mkdir(filepath.Join(queueDir, digest), 0700))
	return w, queueDir, stateDir, digest, now, calls
}

func commitJob(t *testing.T, dir, digest string, user int) {
	t.Helper()
	job := queuedTask{Version: 1, ConfigSHA256: digest, Task: task{UserID: user, TeamID: 9, ChannelID: 59}}
	require.NoError(t, writeAtomicJSON(filepath.Join(dir, digest, stringKey(job.Task)+".json"), job))
}

func stringKey(t task) string {
	return fmtInt(t.UserID) + "-" + fmtInt(t.TeamID) + "-" + fmtInt(t.ChannelID)
}

func fmtInt(i int) string { return strconv.Itoa(i) }

func TestWatchConsumesNewCommittedJobsWithoutRepeatingCompletedSubjects(t *testing.T) {
	w, queueDir, stateDir, digest, now, calls := prepareWatch(t)
	commitJob(t, queueDir, digest, 1)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	idle := 0
	w.Sleep = func(ctx context.Context, d time.Duration) error {
		*now = now.Add(d)
		if d == 5*time.Second {
			idle++
			if idle == 1 {
				commitJob(t, queueDir, digest, 2)
			} else {
				cancel()
				return ctx.Err()
			}
		}
		return nil
	}
	status, err := w.watch(ctx, false)
	require.ErrorIs(t, err, context.Canceled)
	assert.Equal(t, "INTERRUPTED", status)
	require.Len(t, *calls, 2)
	var saved state
	_, err = readPrivateJSON(filepath.Join(stateDir, digest+".json"), &saved)
	require.NoError(t, err)
	assert.Equal(t, "DONE", saved.Tasks["1:9:59"].Status)
	assert.Equal(t, 1, saved.Tasks["1:9:59"].Attempts)
	assert.Equal(t, "DONE", saved.Tasks["2:9:59"].Status)
	var heartbeat heartbeat
	_, err = readPrivateJSON(filepath.Join(stateDir, "heartbeat.json"), &heartbeat)
	require.NoError(t, err)
	assert.Equal(t, digest, heartbeat.ConfigSHA256)
	assert.Equal(t, "idle", heartbeat.Status)
}

func TestWatchFailureRequiresExplicitResumeAndCannotUseAnotherStateToBypassLock(t *testing.T) {
	w, queueDir, stateDir, digest, now, calls := prepareWatch(t)
	commitJob(t, queueDir, digest, 1)
	provision := w.Provision
	w.Provision = func(ctx context.Context, subject service.Sub2APISubject, channel int, sha string) error {
		_ = provision(ctx, subject, channel, sha)
		// The watch owns the installation lock even if a second caller selects
		// a completely different progress file.
		otherQueue := filepath.Join(stateDir, digest+".queue.json")
		status, err := w.run(ctx, otherQueue, filepath.Join(stateDir, "other.json"), false)
		require.Error(t, err)
		assert.Equal(t, "LOCKED", status)
		return errors.New("synthetic network failure")
	}
	status, err := w.watch(context.Background(), false)
	require.Error(t, err)
	assert.Equal(t, "FAILED", status)
	require.Len(t, *calls, 1)
	*now = now.Add(time.Minute)
	status, err = w.watch(context.Background(), false)
	require.Error(t, err)
	assert.Equal(t, "FAILED", status)
	assert.Len(t, *calls, 1)
	w.Provision = provision
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	w.Sleep = func(ctx context.Context, d time.Duration) error { *now = now.Add(d); cancel(); return ctx.Err() }
	_, err = w.watch(ctx, true)
	require.ErrorIs(t, err, context.Canceled)
	assert.Len(t, *calls, 2)
}

func TestWatchHeartbeatContinuesDuringLongProvisioningAndMalformedJobsStop(t *testing.T) {
	w, queueDir, stateDir, digest, _, _ := prepareWatch(t)
	commitJob(t, queueDir, digest, 1)
	var calls atomic.Int32
	w.Provision = func(ctx context.Context, _ service.Sub2APISubject, _ int, _ string) error {
		calls.Add(1)
		var before, after heartbeat
		_, err := readPrivateJSON(filepath.Join(stateDir, "heartbeat.json"), &before)
		require.NoError(t, err)
		assert.Eventually(t, func() bool {
			_, readErr := readPrivateJSON(filepath.Join(stateDir, "heartbeat.json"), &after)
			return readErr == nil && after.LastSeen.After(before.LastSeen)
		}, 5*time.Second, 25*time.Millisecond)
		assert.NoError(t, ctx.Err())
		assert.Equal(t, "running", after.Status)
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	w.Sleep = func(ctx context.Context, _ time.Duration) error { cancel(); return ctx.Err() }
	_, err := w.watchWithHeartbeat(ctx, false, 10*time.Millisecond)
	require.ErrorIs(t, err, context.Canceled)
	assert.Equal(t, int32(1), calls.Load())
	require.NoError(t, os.WriteFile(filepath.Join(queueDir, digest, "bad.json"), []byte(`{"secret":"synthetic-not-a-job"}`), 0600))
	status, err := w.watch(context.Background(), false)
	require.Error(t, err)
	assert.Equal(t, "INVALID_INPUT", status)
	assert.Equal(t, int32(1), calls.Load())
}

func TestWatchBatchRejectsChangedGenerationBeforeAnyProvisioning(t *testing.T) {
	w, queueDir, stateDir, digest, _, calls := prepareWatch(t)
	commitJob(t, queueDir, digest, 1)
	q, err := readTaskSnapshot(queueDir, digest)
	require.NoError(t, err)
	snapshot := filepath.Join(stateDir, digest+".queue.json")
	require.NoError(t, saveSnapshot(snapshot, q))
	configPath, err := canonicalPath(os.Getenv("REALYU_SUB2API_BINDINGS_FILE"))
	require.NoError(t, err)
	lock, err := lockFile(configPath + ".prewarm.lock")
	require.NoError(t, err)
	defer lock.Close()
	// Even a whitespace-only edit is a new immutable config generation. The
	// watcher must never consume generation A's subjects against generation B.
	raw, err := os.ReadFile(configPath)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(configPath, append(raw, '\n'), 0600))
	statePath := filepath.Join(stateDir, digest+".json")
	status, err := w.runBatch(context.Background(), snapshot, statePath, false, configPath, digest)
	require.Error(t, err)
	assert.Equal(t, "INVALID_CONFIG", status)
	assert.Empty(t, *calls)
	_, err = os.Stat(statePath)
	assert.True(t, os.IsNotExist(err))
}

func TestGracefulStopDuringProvisioningRemainsRecoverableWithoutOperatorResume(t *testing.T) {
	w, queuePath, statePath, now, calls := prepareWorker(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	provision := w.Provision
	w.Provision = func(ctx context.Context, subject service.Sub2APISubject, channel int, digest string) error {
		_ = provision(ctx, subject, channel, digest)
		cancel()
		return ctx.Err()
	}
	status, err := w.run(ctx, queuePath, statePath, false)
	require.ErrorIs(t, err, context.Canceled)
	assert.Equal(t, "INTERRUPTED", status)
	var saved state
	_, err = readPrivateJSON(statePath, &saved)
	require.NoError(t, err)
	assert.Equal(t, "RUNNING", saved.Tasks["1:9:59"].Status)
	assert.True(t, saved.NotBefore.After(*now))
	w.Provision = provision
	status, err = w.run(context.Background(), queuePath, statePath, false)
	require.NoError(t, err)
	assert.Equal(t, "DONE", status)
	assert.Len(t, *calls, 3) // One interrupted attempt, then two fixture tasks.
}
