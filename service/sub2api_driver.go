package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
	"github.com/tidwall/gjson"
	"github.com/tidwall/sjson"
	"golang.org/x/sync/singleflight"
)

// Sub2API keeps provider identities separate from the customer ledger. This
// file contains only trusted operator configuration, never customer passwords.
type Sub2APIPool struct {
	ChannelID int    `json:"channel_id"`
	BaseURL   string `json:"base_url"`
	GroupID   int    `json:"group_id"`
}

type Sub2APIBinding struct {
	UserID          int    `json:"realyu_user_id"`
	WorkspaceTeamID int    `json:"workspace_team_id,omitempty"`
	UpstreamUserID  int    `json:"sub2api_user_id"`
	ChannelID       int    `json:"channel_id"`
	GroupID         int    `json:"group_id"`
	BaseURL         string `json:"base_url"`
	APIKey          string `json:"api_key"`
}

// Sub2APISubject is the authenticated member and workspace, not the billing
// owner. Team members can share a payer but must never share retained responses.
type Sub2APISubject struct {
	UserID          int
	WorkspaceTeamID int
}

func Sub2APISubjectFromContext(c *gin.Context) Sub2APISubject {
	userID, teamID := c.GetInt("workspace_user_id"), c.GetInt("workspace_team_id")
	if userID <= 0 {
		if teamID != 0 {
			return Sub2APISubject{}
		}
		userID = c.GetInt("id")
	}
	return Sub2APISubject{UserID: userID, WorkspaceTeamID: teamID}
}

type Sub2APIConfig struct {
	Version        int           `json:"version"`
	Namespace      string        `json:"namespace"`
	IdentitySecret string        `json:"identity_secret"`
	AdminAPIKey    string        `json:"admin_api_key"`
	Pools          []Sub2APIPool `json:"pools"`
	Provision      struct {
		Enabled        bool    `json:"enabled"`
		Mode           string  `json:"mode,omitempty"`
		InitialBalance float64 `json:"initial_balance"`
		Concurrency    int     `json:"concurrency"`
	} `json:"provision"`
	Credit   Sub2APICreditConfig `json:"credit,omitempty"`
	Bindings []Sub2APIBinding    `json:"bindings"`
	digest   string
}

// ConfigSHA256 identifies the exact bytes used by this validated snapshot.
func (cfg *Sub2APIConfig) ConfigSHA256() string { return cfg.digest }

var sub2APIProjectionCache sync.Map
var sub2APIProjectionFlight singleflight.Group
var errSub2APIKeyNotFound = errors.New("Sub2API projected key was not found")
var errSub2APIPrewarmRequired = errors.New("Sub2API customer requires operator prewarming")
var errSub2APIInteractive = errors.New("Sub2API internal identity requires interactive authentication")

// Sub2APIManagementError contains only a status code, never upstream details.
type Sub2APIManagementError struct {
	Status     int
	RetryAfter time.Duration
}

func (e *Sub2APIManagementError) Error() string {
	return fmt.Sprintf("Sub2API management request failed (HTTP %d)", e.Status)
}

func Sub2APIPrewarmFailure(err error) (status string, httpStatus int) {
	var management *Sub2APIManagementError
	if errors.As(err, &management) {
		if management.Status == http.StatusTooManyRequests {
			return "RETRY_WAIT", management.Status
		}
		if management.Status >= 400 && management.Status < 500 {
			return "BLOCKED", management.Status
		}
		return "FAILED", management.Status
	}
	if errors.Is(err, errSub2APIInteractive) {
		return "BLOCKED", 0
	}
	return "FAILED", 0
}

func Sub2APIPrewarmRetryDelay(err error) time.Duration {
	var management *Sub2APIManagementError
	if errors.As(err, &management) {
		return max(time.Minute, min(management.RetryAfter, 24*time.Hour))
	}
	return time.Minute
}

var sub2APIManagementClient = &http.Client{
	Timeout:       15 * time.Second,
	CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
}

func Sub2APIDriverEnabled() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("REALYU_UPSTREAM_DRIVER")), "sub2api")
}

// LoadSub2APIConfig reads the private file again on every request, so missing,
// corrupt or replaced configuration never silently falls back to an old key.
func LoadSub2APIConfig() (*Sub2APIConfig, error) {
	path := strings.TrimSpace(os.Getenv("REALYU_SUB2API_BINDINGS_FILE"))
	if path == "" {
		return nil, errors.New("Sub2API private configuration is missing")
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, errors.New("Sub2API private configuration is unavailable")
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Size() > 16<<20 {
		return nil, errors.New("Sub2API private configuration is invalid")
	}
	data, err := io.ReadAll(io.LimitReader(f, (16<<20)+1))
	if err != nil || len(data) > 16<<20 {
		return nil, errors.New("Sub2API private configuration is invalid")
	}
	var cfg Sub2APIConfig
	if common.Unmarshal(data, &cfg) != nil {
		return nil, errors.New("Sub2API private configuration is invalid")
	}
	if cfg.Version != 1 || cfg.Namespace == "" || len(cfg.Namespace) > 64 || len(cfg.IdentitySecret) < 32 || strings.TrimSpace(cfg.AdminAPIKey) == "" || len(cfg.Pools) == 0 {
		return nil, errors.New("Sub2API private configuration is incomplete")
	}
	if cfg.Provision.Enabled && (math.IsNaN(cfg.Provision.InitialBalance) || math.IsInf(cfg.Provision.InitialBalance, 0) || cfg.Provision.InitialBalance <= 0 || cfg.Provision.InitialBalance > 1_000_000 || cfg.Provision.Concurrency < 1 || cfg.Provision.Concurrency > 10000) {
		return nil, errors.New("Sub2API provisioning budget or concurrency is invalid")
	}
	if cfg.Provision.Mode == "" {
		cfg.Provision.Mode = "prewarmed"
	}
	if cfg.Provision.Mode != "prewarmed" && cfg.Provision.Mode != "isolated-lazy" {
		return nil, errors.New("Sub2API provisioning mode is invalid")
	}
	if cfg.Credit.IdempotencyWindowSeconds == 0 {
		cfg.Credit.IdempotencyWindowSeconds = 3600
	}
	if cfg.Credit.Enabled && (!cfg.Provision.Enabled || cfg.Provision.Mode != "prewarmed" ||
		!validSub2APICreditAmount(cfg.Credit.LowWatermark) || !validSub2APICreditAmount(cfg.Credit.TopupAmount) ||
		cfg.Credit.TopupAmount <= cfg.Credit.LowWatermark || cfg.Credit.CheckIntervalSeconds < 15 || cfg.Credit.CheckIntervalSeconds > 3600 ||
		cfg.Credit.IdempotencyWindowSeconds < 60 || cfg.Credit.IdempotencyWindowSeconds > 82800) {
		return nil, errors.New("Sub2API internal credit policy is invalid")
	}
	pools := make(map[int]Sub2APIPool)
	for i, pool := range cfg.Pools {
		pool.BaseURL = strings.TrimRight(pool.BaseURL, "/")
		u, parseErr := url.Parse(pool.BaseURL)
		if pool.ChannelID <= 0 || pool.GroupID <= 0 || parseErr != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" || (u.Scheme != "http" && u.Scheme != "https") {
			return nil, errors.New("Sub2API pool configuration is invalid")
		}
		if _, exists := pools[pool.ChannelID]; exists {
			return nil, errors.New("Sub2API channel has duplicate pool bindings")
		}
		if cfg.Provision.Mode == "isolated-lazy" {
			ip := net.ParseIP(u.Hostname())
			if ip == nil || !ip.IsLoopback() {
				return nil, errors.New("Sub2API lazy provisioning is restricted to loopback test pools")
			}
		}
		pools[pool.ChannelID], cfg.Pools[i] = pool, pool
	}
	owners, users, keys, pairs := map[int]Sub2APISubject{}, map[Sub2APISubject]int{}, map[string]bool{}, map[string]bool{}
	for _, b := range cfg.Bindings {
		pool, exists := pools[b.ChannelID]
		subject := Sub2APISubject{UserID: b.UserID, WorkspaceTeamID: b.WorkspaceTeamID}
		pair := fmt.Sprintf("%d:%d:%d", b.UserID, b.WorkspaceTeamID, b.ChannelID)
		if !exists || b.UserID <= 0 || b.WorkspaceTeamID < 0 || b.UpstreamUserID <= 0 || b.APIKey == "" || b.GroupID != pool.GroupID || strings.TrimRight(b.BaseURL, "/") != pool.BaseURL || pairs[pair] || keys[b.APIKey] {
			return nil, errors.New("Sub2API user binding is invalid or duplicated")
		}
		if owner := owners[b.UpstreamUserID]; owner.UserID != 0 && owner != subject {
			return nil, errors.New("Sub2API users must not share an upstream identity")
		}
		if owner := users[subject]; owner != 0 && owner != b.UpstreamUserID {
			return nil, errors.New("Sub2API user identity must be stable across pools")
		}
		owners[b.UpstreamUserID], users[subject], keys[b.APIKey], pairs[pair] = subject, b.UpstreamUserID, true, true
	}
	sum := sha256.Sum256(data)
	cfg.digest = hex.EncodeToString(sum[:])
	return &cfg, nil
}

func Sub2APIAdminURL() string {
	raw := strings.TrimSpace(os.Getenv("REALYU_SUB2API_ADMIN_URL"))
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && u.Scheme != "http") {
		return ""
	}
	return u.String()
}

// ResolveSub2APIBinding is invoked after RealYu has authenticated the customer
// and selected an authorized pool. There is intentionally no shared-key fallback.
func ResolveSub2APIBinding(ctx context.Context, subject Sub2APISubject, channelID int, baseURL string) (Sub2APIBinding, error) {
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return Sub2APIBinding{}, err
	}
	if subject.UserID <= 0 || subject.WorkspaceTeamID < 0 {
		return Sub2APIBinding{}, errors.New("Sub2API requires an authenticated customer")
	}
	var pool Sub2APIPool
	for _, candidate := range cfg.Pools {
		if candidate.ChannelID == channelID {
			pool = candidate
			break
		}
	}
	if pool.ChannelID == 0 || pool.BaseURL != strings.TrimRight(baseURL, "/") {
		return Sub2APIBinding{}, errors.New("Sub2API service route is not configured")
	}
	cacheKey := fmt.Sprintf("%s:%d:%d:%d", cfg.digest, subject.UserID, subject.WorkspaceTeamID, channelID)
	if cached, ok := sub2APIProjectionCache.Load(cacheKey); ok {
		return cached.(Sub2APIBinding), nil
	}
	// All pools for one user serialize locally; unique email and custom_key
	// constraints plus a read after create handle competing gateway processes.
	flightKey := fmt.Sprintf("%s:%d:%d", cfg.digest, subject.UserID, subject.WorkspaceTeamID)
	_, err, _ = sub2APIProjectionFlight.Do(flightKey, func() (any, error) {
		ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		if _, ok := sub2APIProjectionCache.Load(cacheKey); ok {
			return nil, nil
		}
		var result Sub2APIBinding
		if err := validateSub2APIPool(ctx, cfg, pool); err != nil {
			return nil, err
		}
		for _, binding := range cfg.Bindings {
			if binding.UserID == subject.UserID && binding.WorkspaceTeamID == subject.WorkspaceTeamID && binding.ChannelID == channelID {
				result = binding
				break
			}
		}
		if result.APIKey != "" {
			if err := verifySub2APIKey(ctx, cfg, pool, result.UpstreamUserID, result.APIKey); err != nil {
				return nil, err
			}
		} else {
			if !cfg.Provision.Enabled {
				return nil, errors.New("Sub2API customer has no configured identity")
			}
			var provisionErr error
			result, provisionErr = resolveSub2APIUser(ctx, cfg, pool, subject, cfg.Provision.Mode == "isolated-lazy")
			if provisionErr != nil {
				if errors.Is(provisionErr, errSub2APIPrewarmRequired) {
					// The pool was selected after gateway authorization. Enqueue a
					// missing custom-key route without doing login work in HTTP.
					_ = QueueSub2APIIdentity(cfg, subject, channelID)
				}
				return nil, provisionErr
			}
		}
		sub2APIProjectionCache.Store(cacheKey, result)
		return nil, nil
	})
	if err != nil {
		return Sub2APIBinding{}, err
	}
	if cached, ok := sub2APIProjectionCache.Load(cacheKey); ok {
		return cached.(Sub2APIBinding), nil
	}
	// A concurrent request may have provisioned a different pool under the
	// shared user flight. Resolve this pool now, rather than borrowing its key.
	return ResolveSub2APIBinding(ctx, subject, channelID, baseURL)
}

func sub2APIIdentity(cfg *Sub2APIConfig, purpose string, subject Sub2APISubject, groupID int) string {
	mac := hmac.New(sha256.New, []byte(cfg.IdentitySecret))
	fmt.Fprintf(mac, "%s\x00%s\x00%d\x00%d", cfg.Namespace, purpose, subject.UserID, groupID)
	if subject.WorkspaceTeamID != 0 {
		fmt.Fprintf(mac, "\x00team:%d", subject.WorkspaceTeamID)
	}
	return hex.EncodeToString(mac.Sum(nil))
}

// Sub2APISessionID namespaces stable session signals by the trusted customer
// identity. The customer cannot choose another tenant's namespace.
func Sub2APISessionID(subject Sub2APISubject, signal string) (string, error) {
	if subject.UserID <= 0 || subject.WorkspaceTeamID < 0 {
		return "", errors.New("Sub2API requires an authenticated customer")
	}
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return "", err
	}
	return sub2APIIdentity(cfg, "session:"+signal, subject, 0), nil
}

func Sub2APIScopeRequestBody(subject Sub2APISubject, data []byte) ([]byte, error) {
	// Server-retained item/conversation lookup has no tenant ownership proof.
	// Full inline history (including encrypted reasoning) remains supported.
	if conversation := gjson.GetBytes(data, "conversation"); conversation.Exists() && conversation.Type != gjson.Null {
		return nil, ErrSub2APIResponseNotOwned
	}
	for _, item := range gjson.GetBytes(data, "input").Array() {
		if item.Get("type").String() == "item_reference" {
			return nil, ErrSub2APIResponseNotOwned
		}
	}
	var err error
	data, err = Sub2APIUnscopeResponseReference(subject, data, "previous_response_id")
	if err != nil {
		return nil, err
	}
	for _, field := range []string{"prompt_cache_key", "conversation_id"} {
		if value := gjson.GetBytes(data, field); value.Exists() && value.Type == gjson.String && value.String() != "" {
			scoped, err := Sub2APISessionID(subject, value.String())
			if err != nil {
				return nil, err
			}
			data, err = sjson.SetBytes(data, field, scoped)
			if err != nil {
				return nil, errors.New("invalid Sub2API session field")
			}
		}
	}
	if value := gjson.GetBytes(data, "client_metadata.x-codex-turn-metadata"); value.Type == gjson.String && value.String() != "" {
		scoped, err := Sub2APIScopeTurnMetadata(subject, value.String())
		if err != nil {
			return nil, err
		}
		data, err = sjson.SetBytes(data, "client_metadata.x-codex-turn-metadata", scoped)
		if err != nil {
			return nil, errors.New("invalid Sub2API turn metadata")
		}
	}
	return data, nil
}

func Sub2APIScopeTurnMetadata(subject Sub2APISubject, metadata string) (string, error) {
	if !gjson.Valid(metadata) {
		return metadata, nil
	}
	parent := gjson.Get(metadata, "parent_thread_id")
	if parent.Type != gjson.String || parent.String() == "" {
		return metadata, nil
	}
	scoped, err := Sub2APISessionID(subject, parent.String())
	if err != nil {
		return "", err
	}
	result, err := sjson.Set(metadata, "parent_thread_id", scoped)
	if err != nil {
		return "", errors.New("invalid Sub2API parent thread metadata")
	}
	return result, nil
}

// FetchSub2APICodexModels preserves upstream model descriptors while retaining
// only names authorized by the outer gateway's group, token and price rules.
func FetchSub2APICodexModels(ctx context.Context, binding Sub2APIBinding, clientVersion string, allowed map[string]bool) ([]common.RawMessage, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, binding.BaseURL+"/v1/models?client_version="+url.QueryEscape(clientVersion), nil)
	if err != nil {
		return nil, errors.New("invalid Codex models request")
	}
	req.Header.Set("Authorization", "Bearer "+binding.APIKey)
	resp, err := sub2APIManagementClient.Do(req)
	if err != nil {
		return nil, errors.New("Sub2API model catalog is unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, errors.New("Sub2API model catalog was rejected")
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (4<<20)+1))
	if err != nil || len(raw) > 4<<20 {
		return nil, errors.New("Sub2API model catalog is invalid")
	}
	var result struct {
		Models []common.RawMessage `json:"models"`
	}
	if common.Unmarshal(raw, &result) != nil || result.Models == nil {
		return nil, errors.New("Sub2API did not return a Codex model catalog")
	}
	models := make([]common.RawMessage, 0, len(result.Models))
	for _, entry := range result.Models {
		if allowed[gjson.GetBytes(entry, "slug").String()] {
			models = append(models, entry)
		}
	}
	return models, nil
}

func sub2APIManagement(ctx context.Context, baseURL, adminKey, jwt, method, path string, input, output any) error {
	return sub2APIManagementWithIdempotency(ctx, baseURL, adminKey, jwt, method, path, "", input, output)
}

func sub2APIManagementWithIdempotency(ctx context.Context, baseURL, adminKey, jwt, method, path, idempotencyKey string, input, output any) error {
	var body io.Reader
	if input != nil {
		raw, err := common.Marshal(input)
		if err != nil {
			return errors.New("Sub2API management request is invalid")
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, baseURL+"/api/v1"+path, body)
	if err != nil {
		return errors.New("Sub2API management request is invalid")
	}
	req.Header.Set("Content-Type", "application/json")
	if idempotencyKey != "" {
		req.Header.Set("Idempotency-Key", idempotencyKey)
	}
	if adminKey != "" {
		req.Header.Set("x-api-key", adminKey)
	}
	if jwt != "" {
		req.Header.Set("Authorization", "Bearer "+jwt)
	}
	resp, err := sub2APIManagementClient.Do(req)
	if err != nil {
		return errors.New("Sub2API management service is unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		failure := &Sub2APIManagementError{Status: resp.StatusCode}
		if seconds, err := strconv.ParseInt(resp.Header.Get("Retry-After"), 10, 32); err == nil && seconds > 0 {
			failure.RetryAfter = time.Duration(seconds) * time.Second
		} else if deadline, err := http.ParseTime(resp.Header.Get("Retry-After")); err == nil {
			failure.RetryAfter = time.Until(deadline)
		}
		return failure
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (4<<20)+1))
	if err != nil || len(raw) > 4<<20 {
		return errors.New("Sub2API management response is invalid")
	}
	var envelope struct {
		Code int               `json:"code"`
		Data common.RawMessage `json:"data"`
	}
	if common.Unmarshal(raw, &envelope) != nil || envelope.Code != 0 {
		return errors.New("Sub2API management operation was rejected")
	}
	if output != nil && common.Unmarshal(envelope.Data, output) != nil {
		return errors.New("Sub2API management response is invalid")
	}
	return nil
}

type sub2APIProjectedUser struct {
	ID                   int      `json:"id"`
	Email                string   `json:"email"`
	Notes                string   `json:"notes"`
	Role                 string   `json:"role"`
	Status               string   `json:"status"`
	AllowedGroups        []int    `json:"allowed_groups"`
	RestrictPublicGroups bool     `json:"restrict_public_groups"`
	Balance              *float64 `json:"balance"`
}
type sub2APIProjectedKey struct {
	UserID  int    `json:"user_id"`
	GroupID int    `json:"group_id"`
	Key     string `json:"key"`
	Status  string `json:"status"`
}

func findSub2APIUser(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool, email string) (sub2APIProjectedUser, error) {
	var page struct {
		Items []sub2APIProjectedUser `json:"items"`
	}
	err := sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodGet, "/admin/users?search="+url.QueryEscape(email)+"&page_size=100", nil, &page)
	if err != nil {
		return sub2APIProjectedUser{}, err
	}
	for _, user := range page.Items {
		if user.Email == email {
			return user, nil
		}
	}
	return sub2APIProjectedUser{}, nil
}

func validateSub2APIPool(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool) error {
	var group struct {
		ID               int    `json:"id"`
		Platform         string `json:"platform"`
		Status           string `json:"status"`
		SubscriptionType string `json:"subscription_type"`
		Fallback         *int   `json:"fallback_group_id"`
		InvalidFallback  *int   `json:"fallback_group_id_on_invalid_request"`
	}
	if err := sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodGet, fmt.Sprintf("/admin/groups/%d", pool.GroupID), nil, &group); err != nil {
		return err
	}
	if group.ID != pool.GroupID || group.Platform != "openai" || group.Status != "active" || group.SubscriptionType != "standard" || (group.Fallback != nil && *group.Fallback != 0) || (group.InvalidFallback != nil && *group.InvalidFallback != 0) {
		return errors.New("Sub2API pool must be active standard OpenAI without cross-group fallback")
	}
	return nil
}

func verifySub2APIKey(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool, upstreamUserID int, key string) error {
	for page := 1; page <= 100; page++ {
		var result struct {
			Items []sub2APIProjectedKey `json:"items"`
			Pages int                   `json:"pages"`
		}
		path := fmt.Sprintf("/admin/users/%d/api-keys?page=%d&page_size=100", upstreamUserID, page)
		if err := sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodGet, path, nil, &result); err != nil {
			return err
		}
		for _, candidate := range result.Items {
			if candidate.Key == key {
				if candidate.UserID != upstreamUserID || candidate.GroupID != pool.GroupID || candidate.Status != "active" {
					return errors.New("Sub2API key ownership, pool or status does not match")
				}
				return nil
			}
		}
		if page >= result.Pages {
			break
		}
	}
	return errSub2APIKeyNotFound
}

// PrewarmSub2APIIdentity is an operator-only offline management operation. It
// never sends inference or touches RealYu's customer database and ledger.
func PrewarmSub2APIIdentity(ctx context.Context, subject Sub2APISubject, channelID int, expectedConfigSHA256 string) error {
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return err
	}
	if expectedConfigSHA256 == "" || cfg.digest != expectedConfigSHA256 {
		return errors.New("Sub2API prewarming configuration generation changed")
	}
	if !cfg.Provision.Enabled || subject.UserID <= 0 || subject.WorkspaceTeamID < 0 {
		return errors.New("Sub2API prewarming is not configured for this subject")
	}
	for _, pool := range cfg.Pools {
		if pool.ChannelID == channelID {
			ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
			defer cancel()
			if err := validateSub2APIPool(ctx, cfg, pool); err != nil {
				return err
			}
			for _, binding := range cfg.Bindings {
				if binding.UserID == subject.UserID && binding.WorkspaceTeamID == subject.WorkspaceTeamID && binding.ChannelID == channelID {
					return verifySub2APIKey(ctx, cfg, pool, binding.UpstreamUserID, binding.APIKey)
				}
			}
			_, err := provisionSub2APIUser(ctx, cfg, pool, subject)
			return err
		}
	}
	return errors.New("Sub2API prewarming pool is not configured")
}

func provisionSub2APIUser(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool, subject Sub2APISubject) (Sub2APIBinding, error) {
	return resolveSub2APIUser(ctx, cfg, pool, subject, true)
}

func resolveSub2APIUser(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool, subject Sub2APISubject, allowCreate bool) (Sub2APIBinding, error) {
	email := sub2APIIdentity(cfg, "email", subject, 0)[:40] + "@realyu.invalid"
	notes := "realyu-projection:" + cfg.Namespace + ":" + strconv.Itoa(subject.UserID)
	if subject.WorkspaceTeamID != 0 {
		notes += ":team:" + strconv.Itoa(subject.WorkspaceTeamID)
	}
	password := sub2APIIdentity(cfg, "password", subject, 0)
	user, err := findSub2APIUser(ctx, cfg, pool, email)
	if err != nil {
		return Sub2APIBinding{}, err
	}
	if user.ID == 0 {
		if !allowCreate {
			return Sub2APIBinding{}, errSub2APIPrewarmRequired
		}
		groups := make([]int, 0, len(cfg.Pools))
		for _, p := range cfg.Pools {
			if p.BaseURL == pool.BaseURL {
				groups = append(groups, p.GroupID)
			}
		}
		input := map[string]any{"email": email, "password": password, "notes": notes, "role": "user", "balance": cfg.Provision.InitialBalance, "concurrency": cfg.Provision.Concurrency, "allowed_groups": groups, "restrict_public_groups": true}
		createErr := sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodPost, "/admin/users", input, &user)
		if createErr != nil {
			user, err = findSub2APIUser(ctx, cfg, pool, email)
			if err != nil || user.ID == 0 {
				return Sub2APIBinding{}, createErr
			}
		}
	}
	if user.ID <= 0 || user.Email != email || user.Notes != notes || user.Role != "user" || user.Status != "active" {
		return Sub2APIBinding{}, errors.New("Sub2API projected identity is inconsistent or disabled")
	}
	if !slices.Contains(user.AllowedGroups, pool.GroupID) || !user.RestrictPublicGroups {
		if !allowCreate {
			return Sub2APIBinding{}, errSub2APIPrewarmRequired
		}
		allowed := append([]int(nil), user.AllowedGroups...)
		for _, configured := range cfg.Pools {
			if configured.BaseURL == pool.BaseURL && !slices.Contains(allowed, configured.GroupID) {
				allowed = append(allowed, configured.GroupID)
			}
		}
		// Concurrent gateways loading the same configuration grant the same
		// complete set, rather than replacing each other's newly added pools.
		// Never reset balances, quotas, credentials or unrelated existing grants.
		if err := sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodPut, fmt.Sprintf("/admin/users/%d", user.ID), map[string]any{"allowed_groups": allowed, "restrict_public_groups": true}, nil); err != nil {
			return Sub2APIBinding{}, err
		}
		verified, err := findSub2APIUser(ctx, cfg, pool, email)
		if err != nil {
			return Sub2APIBinding{}, err
		}
		if verified.ID != user.ID || !verified.RestrictPublicGroups || !slices.Contains(verified.AllowedGroups, pool.GroupID) {
			return Sub2APIBinding{}, errors.New("Sub2API pool authorization changed concurrently; retry")
		}
	}
	key := "sk-" + sub2APIIdentity(cfg, fmt.Sprintf("key:%d", pool.ChannelID), subject, pool.GroupID)
	result := Sub2APIBinding{UserID: subject.UserID, WorkspaceTeamID: subject.WorkspaceTeamID, UpstreamUserID: user.ID, ChannelID: pool.ChannelID, GroupID: pool.GroupID, BaseURL: pool.BaseURL, APIKey: key}
	if keyErr := verifySub2APIKey(ctx, cfg, pool, user.ID, key); keyErr == nil {
		return result, nil
	} else if !errors.Is(keyErr, errSub2APIKeyNotFound) {
		return Sub2APIBinding{}, keyErr
	}
	if !allowCreate {
		return Sub2APIBinding{}, errSub2APIPrewarmRequired
	}
	var login struct {
		AccessToken string `json:"access_token"`
		Requires2FA bool   `json:"requires_2fa"`
	}
	if err := sub2APIManagement(ctx, pool.BaseURL, "", "", http.MethodPost, "/auth/login", map[string]string{"email": email, "password": password}, &login); err != nil {
		return Sub2APIBinding{}, err
	}
	if login.Requires2FA || login.AccessToken == "" {
		return Sub2APIBinding{}, errSub2APIInteractive
	}
	input := map[string]any{"name": "realyu-pool-" + strconv.Itoa(pool.ChannelID), "group_id": pool.GroupID, "custom_key": key}
	// A competing process can create the same deterministic key first. Only a
	// subsequent owner+group+status check may turn that conflict into success.
	createErr := sub2APIManagement(ctx, pool.BaseURL, "", login.AccessToken, http.MethodPost, "/keys", input, nil)
	if err := verifySub2APIKey(ctx, cfg, pool, user.ID, key); err != nil {
		if createErr != nil {
			return Sub2APIBinding{}, createErr
		}
		return Sub2APIBinding{}, err
	}
	return result, nil
}
