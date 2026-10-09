package service

import (
	"context"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
)

// Management projections deliberately exclude upstream credentials, keys,
// customer profiles and costs. None of these reads participates in settlement.
type Sub2APIAccountStats struct {
	Requests int64 `json:"requests"`
	Tokens   int64 `json:"tokens"`
}

type Sub2APIAccountView struct {
	ID                 int                  `json:"id"`
	Name               string               `json:"name"`
	Platform           string               `json:"platform"`
	Type               string               `json:"type"`
	Status             string               `json:"status"`
	Schedulable        bool                 `json:"schedulable"`
	Concurrency        int                  `json:"concurrency"`
	CurrentConcurrency int                  `json:"current_concurrency"`
	FiveHourPercent    *float64             `json:"five_hour_percent"`
	WeeklyPercent      *float64             `json:"weekly_percent"`
	Today              *Sub2APIAccountStats `json:"today"`
}

type Sub2APIPoolView struct {
	ChannelID int                  `json:"channel_id"`
	GroupID   int                  `json:"group_id"`
	GroupName string               `json:"group_name"`
	Accounts  []Sub2APIAccountView `json:"accounts"`
	Available bool                 `json:"available"`
	Truncated bool                 `json:"truncated"`
}

type Sub2APIOverview struct {
	ObservedAt string            `json:"observed_at"`
	Pools      []Sub2APIPoolView `json:"pools"`
}

func GetSub2APIOverview(ctx context.Context) (*Sub2APIOverview, error) {
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	view := &Sub2APIOverview{ObservedAt: time.Now().UTC().Format(time.RFC3339), Pools: make([]Sub2APIPoolView, 0, len(cfg.Pools))}
	for _, pool := range cfg.Pools {
		p := Sub2APIPoolView{ChannelID: pool.ChannelID, GroupID: pool.GroupID, Accounts: []Sub2APIAccountView{}}
		var group struct {
			Name string `json:"name"`
		}
		if sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodGet, "/admin/groups/"+strconv.Itoa(pool.GroupID), nil, &group) == nil {
			p.GroupName = group.Name
		}
		for page := 1; page <= 20; page++ {
			var result struct {
				Items []struct {
					Sub2APIAccountView
					GroupIDs []int `json:"group_ids"`
					Extra    struct {
						FiveHour *float64 `json:"codex_5h_used_percent"`
						Weekly   *float64 `json:"codex_7d_used_percent"`
					} `json:"extra"`
				} `json:"items"`
				Pages int `json:"pages"`
			}
			path := "/admin/accounts?page_size=100&page=" + strconv.Itoa(page) + "&group=" + strconv.Itoa(pool.GroupID)
			if sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodGet, path, nil, &result) != nil {
				p.Available = false
				break
			}
			p.Available = true
			for _, account := range result.Items {
				// Do not trust an upstream version's optional group filter.
				if !slices.Contains(account.GroupIDs, pool.GroupID) {
					continue
				}
				account.FiveHourPercent, account.WeeklyPercent = account.Extra.FiveHour, account.Extra.Weekly
				p.Accounts = append(p.Accounts, account.Sub2APIAccountView)
			}
			p.Truncated = page < result.Pages
			if !p.Truncated {
				break
			}
		}
		// This native POST is a batched read of persisted statistics; it does
		// not refresh OAuth credentials or send an inference request.
		for offset := 0; offset < len(p.Accounts); offset += 100 {
			end := min(offset+100, len(p.Accounts))
			ids := make([]int, 0, end-offset)
			for _, a := range p.Accounts[offset:end] {
				ids = append(ids, a.ID)
			}
			var stats struct {
				Stats map[string]*Sub2APIAccountStats `json:"stats"`
			}
			if sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodPost, "/admin/accounts/today-stats/batch", map[string]any{"account_ids": ids}, &stats) == nil {
				for i := offset; i < end; i++ {
					p.Accounts[i].Today = stats.Stats[strconv.Itoa(p.Accounts[i].ID)]
				}
			}
		}
		view.Pools = append(view.Pools, p)
	}
	return view, nil
}

type Sub2APILogAttribution struct {
	Status        string `json:"status"`
	UsageID       int    `json:"usage_id,omitempty"`
	AccountID     int    `json:"account_id,omitempty"`
	AccountName   string `json:"account_name,omitempty"`
	GroupID       int    `json:"group_id,omitempty"`
	GroupName     string `json:"group_name,omitempty"`
	Model         string `json:"model,omitempty"`
	UpstreamModel string `json:"upstream_model,omitempty"`
}

var sub2APIAttributionCache = struct {
	sync.Mutex
	entries map[string]struct {
		value Sub2APILogAttribution
		until time.Time
	}
}{entries: make(map[string]struct {
	value Sub2APILogAttribution
	until time.Time
})}

// Across concurrent log readers, at most four management lookups are in flight.
var sub2APIAttributionSlots = make(chan struct{}, 4)

func lookupSub2APIAttribution(ctx context.Context, cfg *Sub2APIConfig, pool Sub2APIPool, requestID string) Sub2APILogAttribution {
	result := Sub2APILogAttribution{Status: "not_recorded"}
	if requestID == "" || len(requestID) > 256 {
		return result
	}
	key := cfg.Namespace + "\x00" + pool.BaseURL + "\x00" + strconv.Itoa(pool.GroupID) + "\x00" + requestID
	sub2APIAttributionCache.Lock()
	cached, ok := sub2APIAttributionCache.entries[key]
	sub2APIAttributionCache.Unlock()
	if ok && time.Now().Before(cached.until) {
		return cached.value
	}
	select {
	case sub2APIAttributionSlots <- struct{}{}:
		defer func() { <-sub2APIAttributionSlots }()
	case <-ctx.Done():
		return Sub2APILogAttribution{Status: "unavailable"}
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	var response struct {
		Items []struct {
			ID            int    `json:"id"`
			RequestID     string `json:"request_id"`
			AccountID     int    `json:"account_id"`
			GroupID       int    `json:"group_id"`
			Model         string `json:"model"`
			UpstreamModel string `json:"upstream_model"`
			Account       struct {
				Name string `json:"name"`
			} `json:"account"`
			Group struct {
				Name string `json:"name"`
			} `json:"group"`
		} `json:"items"`
		Total int `json:"total"`
	}
	nativeID := "client:" + requestID
	path := "/admin/usage?page_size=2&group_id=" + strconv.Itoa(pool.GroupID) + "&request_id=" + url.QueryEscape(nativeID)
	if sub2APIManagement(ctx, pool.BaseURL, cfg.AdminAPIKey, "", http.MethodGet, path, nil, &response) != nil {
		result.Status = "unavailable"
	} else if response.Total == 1 && len(response.Items) == 1 {
		item := response.Items[0]
		// Never guess attribution from time, model, route or a substring match.
		if item.RequestID == nativeID && item.GroupID == pool.GroupID && item.AccountID > 0 {
			result = Sub2APILogAttribution{Status: "matched", UsageID: item.ID, AccountID: item.AccountID, AccountName: item.Account.Name, GroupID: item.GroupID, GroupName: item.Group.Name, Model: item.Model, UpstreamModel: item.UpstreamModel}
		}
	}
	ttl := 8 * time.Second
	if result.Status == "matched" {
		ttl = 5 * time.Minute
	}
	sub2APIAttributionCache.Lock()
	if len(sub2APIAttributionCache.entries) >= 4096 {
		clear(sub2APIAttributionCache.entries)
	}
	sub2APIAttributionCache.entries[key] = struct {
		value Sub2APILogAttribution
		until time.Time
	}{result, time.Now().Add(ttl)}
	sub2APIAttributionCache.Unlock()
	return result
}

// EnrichSub2APIAdminLogs adds an ephemeral admin-only projection. Call only on
// the administrative log endpoint, before the existing visibility formatter.
func EnrichSub2APIAdminLogs(ctx context.Context, logs []*model.Log) {
	if !Sub2APIDriverEnabled() {
		return
	}
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return
	}
	pools := make(map[int]Sub2APIPool, len(cfg.Pools))
	for _, pool := range cfg.Pools {
		pools[pool.ChannelID] = pool
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var workers sync.WaitGroup
	for i, log := range logs {
		pool, ok := pools[log.ChannelId]
		if !ok || (log.Type != model.LogTypeConsume && log.Type != model.LogTypeError) {
			continue
		}
		workers.Go(func() {
			attribution := Sub2APILogAttribution{Status: "unavailable"}
			if i < 100 {
				attribution = lookupSub2APIAttribution(ctx, cfg, pool, log.UpstreamRequestId)
			}
			// RawMessage preserves every existing billing value without float conversion.
			other, admin := make(map[string]common.RawMessage), make(map[string]common.RawMessage)
			if log.Other != "" && common.UnmarshalJsonStr(log.Other, &other) != nil {
				return
			}
			if other == nil {
				other = make(map[string]common.RawMessage)
			}
			if raw := other["admin_info"]; len(raw) > 0 && common.Unmarshal(raw, &admin) != nil {
				return
			}
			if admin == nil {
				admin = make(map[string]common.RawMessage)
			}
			admin["sub2api"], _ = common.Marshal(attribution)
			other["admin_info"], _ = common.Marshal(admin)
			if raw, err := common.Marshal(other); err == nil {
				log.Other = string(raw)
			}
		})
	}
	workers.Wait()
}
