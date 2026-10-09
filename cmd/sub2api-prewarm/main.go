// sub2api-prewarm performs serialized operator-managed identity provisioning.
// It never initializes RealYu's database or performs model inference.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
)

type task struct {
	UserID    int `json:"realyu_user_id"`
	TeamID    int `json:"workspace_team_id"`
	ChannelID int `json:"channel_id"`
}

func (t task) key() string { return fmt.Sprintf("%d:%d:%d", t.UserID, t.TeamID, t.ChannelID) }

type queue struct {
	Version int    `json:"version"`
	Tasks   []task `json:"tasks"`
}

type progress struct {
	Status     string `json:"status"`
	Attempts   int    `json:"attempts"`
	HTTPStatus int    `json:"http_status,omitempty"`
}

type state struct {
	Version      int                 `json:"version"`
	ConfigSHA256 string              `json:"config_sha256"`
	QueueSHA256  string              `json:"queue_sha256"`
	NotBefore    time.Time           `json:"not_before"`
	Tasks        map[string]progress `json:"tasks"`
}

type rateState struct {
	NotBefore time.Time `json:"not_before"`
}

type worker struct {
	Now           func() time.Time
	Sleep         func(context.Context, time.Duration) error
	Provision     func(context.Context, service.Sub2APISubject, int, string) error
	InspectCredit func(context.Context, service.Sub2APISubject, int, string) (service.Sub2APICreditAccount, error)
	AddCredit     func(context.Context, service.Sub2APISubject, int, string, int, string) (service.Sub2APICreditAccount, error)
	ResumeCredit  bool
}

func main() {
	queuePath := flag.String("queue", "", "private reviewed subject/pool task list")
	statePath := flag.String("state", "", "private persistent progress file")
	resume := flag.Bool("resume-blocked", false, "explicitly retry after an operator resolves a BLOCKED or FAILED condition")
	watch := flag.Bool("watch", false, "consume the durable queue directory until interrupted")
	resumeCredit := flag.Bool("resume-credit", false, "retry one existing credit intent with its unchanged key after manual reconciliation, within its expiry window")
	flag.Parse()
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	w := worker{Now: time.Now, Sleep: func(ctx context.Context, d time.Duration) error {
		timer := time.NewTimer(d)
		defer timer.Stop()
		select {
		case <-timer.C:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}, Provision: service.PrewarmSub2APIIdentity, InspectCredit: service.InspectSub2APICredit, AddCredit: service.AddSub2APICredit, ResumeCredit: *resumeCredit}
	var status string
	var err error
	if *watch {
		if *queuePath != "" || *statePath != "" {
			status, err = "INVALID_INPUT", errors.New("watch uses queue/state directory environment variables")
		} else {
			status, err = w.watch(ctx, *resume)
		}
	} else {
		if *resumeCredit {
			status, err = "INVALID_INPUT", errors.New("credit reconciliation requires the watch installation")
		} else {
			status, err = w.run(ctx, *queuePath, *statePath, *resume)
		}
	}
	// Never print raw errors, HTTP bodies, keys, credentials or configuration.
	fmt.Println(status)
	if err != nil {
		os.Exit(1)
	}
}

func readPrivateJSON(path string, target any) ([]byte, error) {
	f, err := openPrivateFile(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Size() > 16<<20 {
		return nil, errors.New("invalid private input")
	}
	raw, err := io.ReadAll(io.LimitReader(f, (16<<20)+1))
	if err != nil || len(raw) > 16<<20 {
		return nil, errors.New("invalid private input")
	}
	if target != nil {
		err = common.Unmarshal(raw, target)
	}
	return raw, err
}

func fingerprint(raw []byte) string {
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func canonicalPath(path string) (string, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(abs)
	if err == nil {
		return resolved, nil
	}
	if !os.IsNotExist(err) {
		return "", err
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(abs))
	if err != nil {
		return "", err
	}
	return filepath.Join(parent, filepath.Base(abs)), nil
}

func sameFile(a, b string) bool {
	if a == b || (runtime.GOOS == "windows" && strings.EqualFold(a, b)) {
		return true
	}
	as, ae := os.Stat(a)
	bs, be := os.Stat(b)
	return ae == nil && be == nil && os.SameFile(as, bs)
}

func writeAtomicJSON(path string, value any) error {
	raw, err := common.Marshal(value)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".prewarm-*.tmp")
	if err != nil {
		return err
	}
	temp := f.Name()
	defer os.Remove(temp)
	if _, err = f.Write(raw); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return replaceFile(temp, path)
}

func (w worker) run(ctx context.Context, queuePath, statePath string, resume bool) (string, error) {
	return w.runBatch(ctx, queuePath, statePath, resume, "", "")
}

// heldConfig is set only by watch after acquiring this installation's OS lock.
func (w worker) runBatch(ctx context.Context, queuePath, statePath string, resume bool, heldConfig, expectedGeneration string) (string, error) {
	if queuePath == "" || statePath == "" {
		return "INVALID_INPUT", errors.New("queue and state are required")
	}
	if strings.TrimSpace(os.Getenv("REALYU_SUB2API_BINDINGS_FILE")) == "" {
		return "INVALID_CONFIG", errors.New("missing private config")
	}
	configPath, err := canonicalPath(os.Getenv("REALYU_SUB2API_BINDINGS_FILE"))
	if err != nil {
		return "INVALID_CONFIG", err
	}
	queuePath, err = canonicalPath(queuePath)
	if err != nil {
		return "INVALID_INPUT", err
	}
	statePath, err = canonicalPath(statePath)
	if err != nil {
		return "INVALID_INPUT", err
	}
	paths := []string{configPath, queuePath, statePath, configPath + ".prewarm.lock", configPath + ".prewarm-rate.json"}
	for i := range paths {
		for j := 0; j < i; j++ {
			if sameFile(paths[i], paths[j]) {
				return "INVALID_INPUT", errors.New("private paths overlap")
			}
		}
	}
	if heldConfig == "" {
		lock, err := lockFile(configPath + ".prewarm.lock")
		if err != nil {
			return "LOCKED", err
		}
		defer lock.Close() // The OS releases the lock after normal exit or a crash.
	} else if !sameFile(heldConfig, configPath) {
		return "INVALID_CONFIG", errors.New("configuration lock identity changed")
	}
	cfg, err := service.LoadSub2APIConfig()
	if err != nil {
		return "INVALID_CONFIG", err
	}
	if !cfg.Provision.Enabled {
		return "INVALID_CONFIG", errors.New("provisioning disabled")
	}
	configRaw, err := readPrivateJSON(configPath, nil)
	if err != nil {
		return "INVALID_CONFIG", err
	}
	configDigest := fingerprint(configRaw)
	if cfg.ConfigSHA256() != configDigest || (expectedGeneration != "" && expectedGeneration != configDigest) {
		return "INVALID_CONFIG", errors.New("configuration generation changed before batch")
	}
	var q queue
	queueRaw, err := readPrivateJSON(queuePath, &q)
	if err != nil || q.Version != 1 || len(q.Tasks) == 0 || len(q.Tasks) > 100000 {
		return "INVALID_INPUT", errors.New("invalid queue")
	}
	seen := map[string]bool{}
	for _, t := range q.Tasks {
		validPool := false
		for _, pool := range cfg.Pools {
			if pool.ChannelID == t.ChannelID {
				validPool = true
				break
			}
		}
		if t.UserID <= 0 || t.TeamID < 0 || !validPool || seen[t.key()] {
			return "INVALID_INPUT", errors.New("invalid or duplicate subject/pool")
		}
		seen[t.key()] = true
	}
	s := state{Version: 1, ConfigSHA256: fingerprint(configRaw), Tasks: map[string]progress{}}
	if _, err := readPrivateJSON(statePath, &s); err != nil && !os.IsNotExist(err) {
		return "INVALID_STATE", err
	}
	if s.Version != 1 || s.ConfigSHA256 != fingerprint(configRaw) || s.Tasks == nil {
		return "INVALID_STATE", errors.New("configuration generation changed")
	}
	s.QueueSHA256 = fingerprint(queueRaw)
	ratePath := configPath + ".prewarm-rate.json"
	var rate rateState
	if _, err := readPrivateJSON(ratePath, &rate); err != nil && !os.IsNotExist(err) {
		return "INVALID_STATE", err
	}
	// A previous 429's durable deadline also applies to a different queue or
	// a process restart using the same private installation configuration.
	if rate.NotBefore.After(s.NotBefore) {
		s.NotBefore = rate.NotBefore
	}
	if s.NotBefore.Sub(w.Now()) > 4*time.Second {
		return "RETRY_WAIT", errors.New("durable retry deadline pending")
	}
	for _, t := range q.Tasks {
		p := s.Tasks[t.key()]
		if p.Status == "DONE" {
			continue
		}
		if (p.Status == "BLOCKED" || p.Status == "FAILED") && !resume {
			return p.Status, errors.New("operator resume required")
		}
		if err := ctx.Err(); err != nil {
			return "INTERRUPTED", err
		}
		if p.Status == "RUNNING" {
			// An interrupted task may have logged in just before the crash.
			// Recover conservatively; creation itself remains idempotent.
			s.NotBefore = w.Now().Add(4 * time.Second)
		}
		if wait := s.NotBefore.Sub(w.Now()); wait > 0 {
			if err := w.Sleep(ctx, wait); err != nil {
				return "INTERRUPTED", err
			}
		}
		// Refuse edits to configuration or the task list while a batch is active.
		current, err := readPrivateJSON(configPath, nil)
		if err != nil || fingerprint(current) != s.ConfigSHA256 {
			return "INVALID_CONFIG", errors.New("configuration changed during batch")
		}
		current, err = readPrivateJSON(queuePath, nil)
		if err != nil || fingerprint(current) != s.QueueSHA256 {
			return "INVALID_INPUT", errors.New("queue changed during batch")
		}
		p.Status, p.HTTPStatus, p.Attempts = "RUNNING", 0, p.Attempts+1
		s.Tasks[t.key()] = p
		s.NotBefore = w.Now().Add(4 * time.Second).UTC()
		// Persist the cooldown before any side effect. A crash cannot skip it.
		if err := writeAtomicJSON(ratePath, rateState{s.NotBefore}); err != nil {
			return "STATE_WRITE_FAILED", err
		}
		if err := writeAtomicJSON(statePath, s); err != nil {
			return "STATE_WRITE_FAILED", err
		}
		err = w.Provision(ctx, service.Sub2APISubject{UserID: t.UserID, WorkspaceTeamID: t.TeamID}, t.ChannelID, s.ConfigSHA256)
		interrupted := ctx.Err() != nil
		if interrupted {
			// A graceful stop can arrive after an upstream side effect. Keep the
			// same recoverable state as a process crash; a new worker will pace
			// and reconcile the identity idempotently, without an operator retry.
			p.Status, p.HTTPStatus = "RUNNING", 0
		} else if err == nil {
			p.Status = "DONE"
		} else {
			p.Status, p.HTTPStatus = service.Sub2APIPrewarmFailure(err)
		}
		s.NotBefore = w.Now().Add(4 * time.Second).UTC()
		if p.Status == "RETRY_WAIT" {
			s.NotBefore = w.Now().Add(service.Sub2APIPrewarmRetryDelay(err)).UTC()
		}
		if saveErr := writeAtomicJSON(ratePath, rateState{s.NotBefore}); saveErr != nil {
			return "STATE_WRITE_FAILED", saveErr
		}
		s.Tasks[t.key()] = p
		if saveErr := writeAtomicJSON(statePath, s); saveErr != nil {
			return "STATE_WRITE_FAILED", saveErr
		}
		if interrupted {
			return "INTERRUPTED", ctx.Err()
		}
		if err != nil {
			return p.Status, err
		}
	}
	return "DONE", nil
}
