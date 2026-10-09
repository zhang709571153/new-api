package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func prepareCredit(t *testing.T) (worker, *service.Sub2APIConfig, queue, string, *time.Time) {
	t.Helper()
	w, _, stateDir, _, now, _ := prepareWatch(t)
	path := os.Getenv("REALYU_SUB2API_BINDINGS_FILE")
	var input map[string]any
	_, err := readPrivateJSON(path, &input)
	require.NoError(t, err)
	input["credit"] = map[string]any{"enabled": true, "low_watermark": 20, "topup_amount": 100, "check_interval_seconds": 60, "idempotency_window_seconds": 3600}
	require.NoError(t, writeAtomicJSON(path, input))
	cfg, err := service.LoadSub2APIConfig()
	require.NoError(t, err)
	q := queue{Version: 1, Tasks: []task{{UserID: 1, TeamID: 9, ChannelID: 59}, {UserID: 2, TeamID: 9, ChannelID: 59}}}
	progress := state{Version: 1, ConfigSHA256: cfg.ConfigSHA256(), Tasks: map[string]progress{q.Tasks[0].key(): {Status: "DONE"}, q.Tasks[1].key(): {Status: "RUNNING"}}}
	require.NoError(t, writeAtomicJSON(filepath.Join(stateDir, cfg.ConfigSHA256()+".json"), progress))
	return w, cfg, q, stateDir, now
}

func creditAccount(id int, balance float64) service.Sub2APICreditAccount {
	return service.Sub2APICreditAccount{UpstreamUserID: id, Balance: balance, BaseURL: "http://127.0.0.1:9999"}
}

func TestCreditPersistsIntentBeforeAddAndOnlyChecksDoneIdentities(t *testing.T) {
	w, cfg, q, dir, now := prepareCredit(t)
	balance := 1.0
	adds, reads := 0, 0
	w.InspectCredit = func(_ context.Context, s service.Sub2APISubject, _ int, digest string) (service.Sub2APICreditAccount, error) {
		reads++
		assert.Equal(t, 1, s.UserID)
		assert.Equal(t, cfg.ConfigSHA256(), digest)
		return creditAccount(17, balance), nil
	}
	w.AddCredit = func(_ context.Context, _ service.Sub2APISubject, _ int, _ string, id int, key string) (service.Sub2APICreditAccount, error) {
		adds++
		var audit creditLedger
		_, err := readPrivateJSON(filepath.Join(dir, "credit.json"), &audit)
		require.NoError(t, err)
		require.Len(t, audit.Operations, 1)
		assert.Equal(t, key, audit.Operations[0].ID)
		assert.Equal(t, "IN_FLIGHT", audit.Operations[0].Status)
		assert.Equal(t, 1, audit.Operations[0].Attempts)
		assert.Equal(t, 100.0, audit.Operations[0].Amount)
		balance += 100
		return creditAccount(id, balance), nil
	}
	status, err := w.creditCycle(context.Background(), cfg, q, dir, false)
	require.NoError(t, err)
	assert.Equal(t, "ready", status)
	_, err = w.creditCycle(context.Background(), cfg, q, dir, false)
	require.NoError(t, err)
	assert.Equal(t, 1, reads)
	*now = now.Add(time.Minute)
	_, err = w.creditCycle(context.Background(), cfg, q, dir, false)
	require.NoError(t, err)
	assert.Equal(t, 1, adds)
	var saved creditLedger
	raw, err := readPrivateJSON(filepath.Join(dir, "credit.json"), &saved)
	require.NoError(t, err)
	assert.Equal(t, "CONFIRMED", saved.Operations[0].Status)
	assert.Equal(t, 101.0, saved.Operations[0].AfterBalance)
	assert.NotContains(t, string(raw), "test-admin")
	assert.NotContains(t, string(raw), "identity_secret")
}

func TestCreditLostResponseBlocksRestartAndExplicitReplayKeepsOperation(t *testing.T) {
	w, cfg, q, dir, _ := prepareCredit(t)
	w.InspectCredit = func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error) {
		return creditAccount(17, 1), nil
	}
	ids := []string{}
	applied := map[string]bool{}
	credits := 0
	w.AddCredit = func(_ context.Context, _ service.Sub2APISubject, _ int, _ string, id int, key string) (service.Sub2APICreditAccount, error) {
		ids = append(ids, key)
		if !applied[key] {
			applied[key] = true
			credits++
			return service.Sub2APICreditAccount{}, errors.New("synthetic response lost after commit")
		}
		return creditAccount(id, 101), nil
	}
	status, err := w.creditCycle(context.Background(), cfg, q, dir, false)
	require.Error(t, err)
	assert.Equal(t, "blocked", status)
	status, err = w.creditCycle(context.Background(), cfg, q, dir, false)
	require.Error(t, err)
	assert.Equal(t, "blocked", status)
	require.Len(t, ids, 1)
	status, err = w.creditCycle(context.Background(), cfg, q, dir, true)
	require.NoError(t, err)
	assert.Equal(t, "ready", status)
	require.Len(t, ids, 2)
	assert.Equal(t, ids[0], ids[1])
	assert.Equal(t, 1, credits)
}

func TestCreditUnknownIntentCannotCrossConfigOrIdempotencyWindow(t *testing.T) {
	for _, kind := range []string{"expired", "config_changed", "interrupted"} {
		t.Run(kind, func(t *testing.T) {
			w, cfg, q, dir, now := prepareCredit(t)
			w.InspectCredit = func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error) {
				return creditAccount(17, 1), nil
			}
			calls := 0
			w.AddCredit = func(context.Context, service.Sub2APISubject, int, string, int, string) (service.Sub2APICreditAccount, error) {
				calls++
				return service.Sub2APICreditAccount{}, errors.New("synthetic uncertainty")
			}
			_, err := w.creditCycle(context.Background(), cfg, q, dir, false)
			require.Error(t, err)
			switch kind {
			case "expired":
				*now = now.Add(2 * time.Hour)
			case "config_changed":
				p := os.Getenv("REALYU_SUB2API_BINDINGS_FILE")
				raw, err := os.ReadFile(p)
				require.NoError(t, err)
				require.NoError(t, os.WriteFile(p, append(raw, '\n'), 0600))
				cfg, err = service.LoadSub2APIConfig()
				require.NoError(t, err)
			case "interrupted":
				var audit creditLedger
				_, err := readPrivateJSON(filepath.Join(dir, "credit.json"), &audit)
				require.NoError(t, err)
				audit.Operations[0].Status = "IN_FLIGHT"
				require.NoError(t, writeAtomicJSON(filepath.Join(dir, "credit.json"), audit))
			}
			_, err = w.creditCycle(context.Background(), cfg, q, dir, kind != "interrupted")
			require.Error(t, err)
			assert.Equal(t, 1, calls)
		})
	}
}

func TestCredit429BackoffAndBoundedDuplicateUserScanning(t *testing.T) {
	t.Run("same key after 429", func(t *testing.T) {
		w, cfg, q, dir, now := prepareCredit(t)
		ids := []string{}
		w.InspectCredit = func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error) {
			return creditAccount(17, 1), nil
		}
		w.AddCredit = func(_ context.Context, _ service.Sub2APISubject, _ int, _ string, id int, key string) (service.Sub2APICreditAccount, error) {
			ids = append(ids, key)
			if len(ids) == 1 {
				return service.Sub2APICreditAccount{}, &service.Sub2APIManagementError{Status: 429}
			}
			return creditAccount(id, 101), nil
		}
		status, err := w.creditCycle(context.Background(), cfg, q, dir, false)
		require.NoError(t, err)
		assert.Equal(t, "checking", status)
		_, err = w.creditCycle(context.Background(), cfg, q, dir, false)
		require.NoError(t, err)
		require.Len(t, ids, 1)
		*now = now.Add(time.Minute)
		_, err = w.creditCycle(context.Background(), cfg, q, dir, false)
		require.NoError(t, err)
		require.Len(t, ids, 2)
		assert.Equal(t, ids[0], ids[1])
	})
	t.Run("duplicate user cannot bypass page bound", func(t *testing.T) {
		w, cfg, q, dir, now := prepareCredit(t)
		q.Tasks = nil
		saved := state{Version: 1, ConfigSHA256: cfg.ConfigSHA256(), Tasks: map[string]progress{}}
		for i := 1; i <= 60; i++ {
			entry := task{UserID: i, TeamID: 9, ChannelID: 59}
			q.Tasks = append(q.Tasks, entry)
			saved.Tasks[entry.key()] = progress{Status: "DONE"}
		}
		require.NoError(t, writeAtomicJSON(filepath.Join(dir, cfg.ConfigSHA256()+".json"), saved))
		reads := 0
		w.InspectCredit = func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error) {
			reads++
			return creditAccount(17, 100), nil
		}
		w.AddCredit = func(context.Context, service.Sub2APISubject, int, string, int, string) (service.Sub2APICreditAccount, error) {
			t.Fatal("unexpected add")
			return service.Sub2APICreditAccount{}, nil
		}
		status, err := w.creditCycle(context.Background(), cfg, q, dir, false)
		require.NoError(t, err)
		assert.Equal(t, "checking", status)
		assert.Equal(t, 50, reads)
		*now = now.Add(5 * time.Second)
		status, err = w.creditCycle(context.Background(), cfg, q, dir, false)
		require.NoError(t, err)
		assert.Equal(t, "ready", status)
		assert.Equal(t, 60, reads)
	})
}

func TestCreditInspectionFailuresPersistBackoffAndOperatorBlocks(t *testing.T) {
	for _, code := range []int{429, 401, 403, 423, 500} {
		t.Run(time.Duration(code).String(), func(t *testing.T) {
			w, cfg, q, dir, now := prepareCredit(t)
			reads := 0
			w.InspectCredit = func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error) {
				reads++
				return service.Sub2APICreditAccount{}, &service.Sub2APIManagementError{Status: code, RetryAfter: 2 * time.Minute}
			}
			w.AddCredit = func(context.Context, service.Sub2APISubject, int, string, int, string) (service.Sub2APICreditAccount, error) {
				t.Fatal("unexpected add")
				return service.Sub2APICreditAccount{}, nil
			}
			_, err := w.creditCycle(context.Background(), cfg, q, dir, false)
			require.Error(t, err)
			_, _ = w.creditCycle(context.Background(), cfg, q, dir, false)
			assert.Equal(t, 1, reads)
			*now = now.Add(90 * time.Second)
			_, _ = w.creditCycle(context.Background(), cfg, q, dir, false)
			if code == 500 {
				assert.Equal(t, 2, reads)
			} else {
				assert.Equal(t, 1, reads)
			}
			if code == 429 {
				*now = now.Add(time.Minute)
				_, _ = w.creditCycle(context.Background(), cfg, q, dir, false)
				assert.Equal(t, 2, reads)
			}
			if code == 401 || code == 403 || code == 423 {
				w.InspectCredit = func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error) {
					reads++
					return creditAccount(17, 100), nil
				}
				status, err := w.creditCycle(context.Background(), cfg, q, dir, true)
				require.NoError(t, err)
				assert.Equal(t, "ready", status)
				assert.Equal(t, 2, reads)
			}
		})
	}
}
