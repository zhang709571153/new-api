package service

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"slices"
	"strconv"
)

// Internal credit is only an operational allowance in Sub2API. It never reads
// or writes a RealYu wallet, subscription, quota, payment or usage ledger.
type Sub2APICreditConfig struct {
	Enabled                  bool    `json:"enabled"`
	LowWatermark             float64 `json:"low_watermark"`
	TopupAmount              float64 `json:"topup_amount"`
	CheckIntervalSeconds     int     `json:"check_interval_seconds"`
	IdempotencyWindowSeconds int     `json:"idempotency_window_seconds,omitempty"`
}

type Sub2APICreditAccount struct {
	UpstreamUserID int
	Balance        float64
	BaseURL        string
}

type sub2APICreditUncertainError struct{ cause error }

func (e *sub2APICreditUncertainError) Error() string {
	return "Sub2API internal credit result is uncertain; operator reconciliation is required"
}
func (e *sub2APICreditUncertainError) Unwrap() error { return e.cause }

func Sub2APICreditOutcomeUncertain(err error) bool {
	var uncertain *sub2APICreditUncertainError
	return errors.As(err, &uncertain)
}

func validSub2APICreditAmount(amount float64) bool {
	return amount > 0 && amount <= 1_000_000 && !math.IsNaN(amount) && !math.IsInf(amount, 0)
}

func sub2APICreditIdentity(cfg *Sub2APIConfig, subject Sub2APISubject) (email, notes string) {
	email = sub2APIIdentity(cfg, "email", subject, 0)[:40] + "@realyu.invalid"
	notes = "realyu-projection:" + cfg.Namespace + ":" + strconv.Itoa(subject.UserID)
	if subject.WorkspaceTeamID != 0 {
		notes += ":team:" + strconv.Itoa(subject.WorkspaceTeamID)
	}
	return
}

func sub2APICreditSnapshot(subject Sub2APISubject, channelID int, expectedSHA string) (*Sub2APIConfig, Sub2APIPool, error) {
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return nil, Sub2APIPool{}, err
	}
	if expectedSHA == "" || cfg.digest != expectedSHA || !cfg.Credit.Enabled || subject.UserID <= 0 || subject.WorkspaceTeamID < 0 {
		return nil, Sub2APIPool{}, errors.New("Sub2API internal credit configuration or subject is unavailable")
	}
	for _, pool := range cfg.Pools {
		if pool.ChannelID == channelID {
			return cfg, pool, nil
		}
	}
	return nil, Sub2APIPool{}, errors.New("Sub2API internal credit pool is unavailable")
}

func validSub2APICreditUser(cfg *Sub2APIConfig, pool Sub2APIPool, subject Sub2APISubject, user sub2APIProjectedUser) bool {
	email, notes := sub2APICreditIdentity(cfg, subject)
	return user.ID > 0 && user.Email == email && user.Notes == notes && user.Role == "user" && user.Status == "active" &&
		user.RestrictPublicGroups && slices.Contains(user.AllowedGroups, pool.GroupID) && user.Balance != nil &&
		!math.IsNaN(*user.Balance) && !math.IsInf(*user.Balance, 0)
}

func inspectSub2APICredit(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool, subject Sub2APISubject) (Sub2APICreditAccount, error) {
	if err := validateSub2APIPool(ctx, cfg, pool); err != nil {
		return Sub2APICreditAccount{}, err
	}
	email, _ := sub2APICreditIdentity(cfg, subject)
	user, err := findSub2APIUser(ctx, cfg, pool, email)
	if err != nil {
		return Sub2APICreditAccount{}, err
	}
	if !validSub2APICreditUser(cfg, pool, subject, user) {
		return Sub2APICreditAccount{}, errors.New("Sub2API internal credit identity is not an active managed projection")
	}
	key := "sk-" + sub2APIIdentity(cfg, fmt.Sprintf("key:%d", pool.ChannelID), subject, pool.GroupID)
	if err := verifySub2APIKey(ctx, cfg, pool, user.ID, key); err != nil {
		return Sub2APICreditAccount{}, err
	}
	return Sub2APICreditAccount{UpstreamUserID: user.ID, Balance: *user.Balance, BaseURL: pool.BaseURL}, nil
}

// InspectSub2APICredit never creates a user or key and never logs a user in.
func InspectSub2APICredit(ctx context.Context, subject Sub2APISubject, channelID int, expectedConfigSHA256 string) (Sub2APICreditAccount, error) {
	cfg, pool, err := sub2APICreditSnapshot(subject, channelID, expectedConfigSHA256)
	if err != nil {
		return Sub2APICreditAccount{}, err
	}
	return inspectSub2APICredit(ctx, cfg, pool, subject)
}

// AddSub2APICredit requires a durably recorded worker intent. Callers must stop
// on uncertain outcomes: upstream idempotency has a finite retention window
// and cannot make an interrupted balance mutation unconditionally exactly once.
func AddSub2APICredit(ctx context.Context, subject Sub2APISubject, channelID int, expectedConfigSHA256 string, expectedUpstreamUserID int, idempotencyKey string) (Sub2APICreditAccount, error) {
	if len(idempotencyKey) < 16 || len(idempotencyKey) > 128 {
		return Sub2APICreditAccount{}, errors.New("Sub2API internal credit operation identifier is invalid")
	}
	for _, c := range idempotencyKey {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return Sub2APICreditAccount{}, errors.New("Sub2API internal credit operation identifier is invalid")
		}
	}
	cfg, pool, err := sub2APICreditSnapshot(subject, channelID, expectedConfigSHA256)
	if err != nil {
		return Sub2APICreditAccount{}, err
	}
	account, err := inspectSub2APICredit(ctx, cfg, pool, subject)
	if err != nil {
		return Sub2APICreditAccount{}, err
	}
	if expectedUpstreamUserID <= 0 || account.UpstreamUserID != expectedUpstreamUserID {
		return Sub2APICreditAccount{}, errors.New("Sub2API internal credit identity changed")
	}
	var updated sub2APIProjectedUser
	input := map[string]any{"operation": "add", "balance": cfg.Credit.TopupAmount, "notes": "realyu-internal-credit:" + cfg.Namespace + ":" + idempotencyKey}
	err = sub2APIManagementWithIdempotency(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodPost,
		fmt.Sprintf("/admin/users/%d/balance", account.UpstreamUserID), idempotencyKey, input, &updated)
	if err != nil {
		var management *Sub2APIManagementError
		if errors.As(err, &management) && management.Status >= 400 && management.Status < 500 && management.Status != http.StatusRequestTimeout {
			return Sub2APICreditAccount{}, err
		}
		return Sub2APICreditAccount{}, &sub2APICreditUncertainError{cause: err}
	}
	if updated.ID != account.UpstreamUserID || !validSub2APICreditUser(cfg, pool, subject, updated) {
		return Sub2APICreditAccount{}, &sub2APICreditUncertainError{cause: errors.New("Sub2API internal credit response is inconsistent")}
	}
	return Sub2APICreditAccount{UpstreamUserID: updated.ID, Balance: *updated.Balance, BaseURL: pool.BaseURL}, nil
}
