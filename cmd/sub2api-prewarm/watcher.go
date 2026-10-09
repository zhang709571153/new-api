package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
)

type queuedTask struct {
	Version      int    `json:"version"`
	ConfigSHA256 string `json:"config_sha256"`
	Task         task   `json:"task"`
}

type heartbeat struct {
	Version      int       `json:"version"`
	ConfigSHA256 string    `json:"config_sha256"`
	LastSeen     time.Time `json:"last_seen"`
	Status       string    `json:"status"`
	CreditStatus string    `json:"credit_status,omitempty"`
}

// The heartbeat has a separate file, so it never rewrites in-flight progress.
type heartbeatWriter struct {
	mu    sync.Mutex
	path  string
	value heartbeat
}

func (h *heartbeatWriter) write(digest, status string) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if digest != "" {
		h.value.ConfigSHA256 = digest
	}
	if status != "" {
		h.value.Status = status
	}
	h.value.Version = 1
	h.value.LastSeen = time.Now().UTC()
	return writeAtomicJSON(h.path, h.value)
}

func (h *heartbeatWriter) credit(digest, status string) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.value.ConfigSHA256, h.value.CreditStatus = digest, status
	h.value.Version, h.value.LastSeen = 1, time.Now().UTC()
	return writeAtomicJSON(h.path, h.value)
}

func watchDirectories() (configPath, queueDir, stateDir string, err error) {
	values := []string{os.Getenv("REALYU_SUB2API_BINDINGS_FILE"), os.Getenv("REALYU_SUB2API_QUEUE_DIR"), os.Getenv("REALYU_SUB2API_STATE_DIR")}
	paths := make([]string, len(values))
	for i, value := range values {
		if strings.TrimSpace(value) == "" {
			return "", "", "", errors.New("watch paths are required")
		}
		paths[i], err = canonicalPath(value)
		if err != nil {
			return "", "", "", err
		}
	}
	for _, path := range paths[1:] {
		st, statErr := os.Stat(path)
		if statErr != nil || !st.IsDir() {
			return "", "", "", errors.New("watch directories must already exist")
		}
	}
	if sameFile(paths[1], paths[2]) {
		return "", "", "", errors.New("queue and state directories must differ")
	}
	return paths[0], paths[1], paths[2], nil
}

func readTaskSnapshot(queueDir, digest string) (queue, error) {
	q := queue{Version: 1, Tasks: []task{}}
	dir := filepath.Join(queueDir, digest)
	st, err := os.Lstat(dir)
	if os.IsNotExist(err) {
		return q, nil
	}
	if err != nil || !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return q, errors.New("queue generation directory is invalid")
	}
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return q, nil
	}
	if err != nil || len(entries) > 100000 {
		return q, errors.New("queue directory unavailable or oversized")
	}
	seen := map[string]bool{}
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue // Producer temp files are not yet committed jobs.
		}
		if entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			return q, errors.New("queue job must be a regular file")
		}
		var job queuedTask
		if _, err := readPrivateJSON(filepath.Join(dir, entry.Name()), &job); err != nil {
			return q, errors.New("invalid queue job")
		}
		t := job.Task
		if job.Version != 1 || job.ConfigSHA256 != digest || t.UserID <= 0 || t.TeamID < 0 || t.ChannelID <= 0 || seen[t.key()] || entry.Name() != strings.ReplaceAll(t.key(), ":", "-")+".json" {
			return q, errors.New("queue job identity or generation mismatch")
		}
		seen[t.key()] = true
		q.Tasks = append(q.Tasks, t)
	}
	sort.Slice(q.Tasks, func(i, j int) bool { return q.Tasks[i].key() < q.Tasks[j].key() })
	return q, nil
}

func saveSnapshot(path string, q queue) error {
	raw, err := common.Marshal(q)
	if err != nil {
		return err
	}
	current, readErr := readPrivateJSON(path, nil)
	if readErr == nil && fingerprint(current) == fingerprint(raw) {
		return nil
	}
	if readErr != nil && !os.IsNotExist(readErr) {
		return readErr
	}
	return writeAtomicJSON(path, q)
}

func (w worker) watch(ctx context.Context, resume bool) (string, error) {
	return w.watchWithHeartbeat(ctx, resume, 15*time.Second)
}

func (w worker) watchWithHeartbeat(ctx context.Context, resume bool, heartbeatEvery time.Duration) (string, error) {
	configPath, queueDir, stateDir, err := watchDirectories()
	if err != nil {
		return "INVALID_INPUT", err
	}
	lock, err := lockFile(configPath + ".prewarm.lock")
	if err != nil {
		return "LOCKED", err
	}
	defer lock.Close()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	h := &heartbeatWriter{path: filepath.Join(stateDir, "heartbeat.json")}
	if sameFile(h.path, configPath) || sameFile(h.path, configPath+".prewarm.lock") || sameFile(h.path, configPath+".prewarm-rate.json") {
		return "INVALID_INPUT", errors.New("heartbeat overlaps private input")
	}
	if err := h.write("", "running"); err != nil {
		return "STATE_WRITE_FAILED", err
	}
	heartbeatFailed := make(chan error, 1)
	heartbeatDone := make(chan struct{})
	go func() {
		defer close(heartbeatDone)
		ticker := time.NewTicker(heartbeatEvery)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := h.write("", ""); err != nil {
					heartbeatFailed <- err
					cancel()
					return
				}
			}
		}
	}()
	defer func() { cancel(); <-heartbeatDone }()
	creditResumeAvailable := w.ResumeCredit
	for {
		select {
		case failure := <-heartbeatFailed:
			return "STATE_WRITE_FAILED", failure
		default:
		}
		if err := ctx.Err(); err != nil {
			return "INTERRUPTED", err
		}
		cfg, err := service.LoadSub2APIConfig()
		if err != nil || !cfg.Provision.Enabled {
			_ = h.write("", "error")
			return "INVALID_CONFIG", errors.New("configuration unavailable or provisioning disabled")
		}
		raw, err := readPrivateJSON(configPath, nil)
		if err != nil {
			_ = h.write("", "error")
			return "INVALID_CONFIG", err
		}
		digest := fingerprint(raw)
		if cfg.ConfigSHA256() != digest {
			_ = h.write("", "error")
			return "INVALID_CONFIG", errors.New("configuration generation changed during scan")
		}
		q, err := readTaskSnapshot(queueDir, digest)
		if err != nil {
			_ = h.write(digest, "error")
			return "INVALID_INPUT", err
		}
		creditStatus, _ := w.creditCycle(ctx, cfg, q, stateDir, creditResumeAvailable)
		creditResumeAvailable = false // One explicitly reconciled intent, not a permanent bypass.
		if err := h.credit(digest, creditStatus); err != nil {
			return "STATE_WRITE_FAILED", err
		}
		status := "DONE"
		if len(q.Tasks) > 0 {
			snapshot := filepath.Join(stateDir, digest+".queue.json")
			statePath := filepath.Join(stateDir, digest+".json")
			for _, path := range []string{snapshot, statePath} {
				if sameFile(path, configPath) || sameFile(path, configPath+".prewarm.lock") || sameFile(path, configPath+".prewarm-rate.json") {
					return "INVALID_INPUT", errors.New("watch output overlaps input")
				}
			}
			if err := saveSnapshot(snapshot, q); err != nil {
				return "STATE_WRITE_FAILED", err
			}
			if err := h.write(digest, "running"); err != nil {
				return "STATE_WRITE_FAILED", err
			}
			status, err = w.runBatch(ctx, snapshot, statePath, resume, configPath, digest)
			select {
			case failure := <-heartbeatFailed:
				return "STATE_WRITE_FAILED", failure
			default:
			}
			if err != nil && status != "RETRY_WAIT" {
				heartbeatStatus := "error"
				if status == "BLOCKED" {
					heartbeatStatus = "blocked"
				}
				_ = h.write(digest, heartbeatStatus)
				return status, err
			}
		}
		heartbeatStatus := "idle"
		if status == "RETRY_WAIT" {
			heartbeatStatus = "running"
		}
		if err := h.write(digest, heartbeatStatus); err != nil {
			return "STATE_WRITE_FAILED", err
		}
		if err := w.Sleep(ctx, 5*time.Second); err != nil {
			select {
			case failure := <-heartbeatFailed:
				return "STATE_WRITE_FAILED", failure
			default:
			}
			return "INTERRUPTED", err
		}
	}
}
