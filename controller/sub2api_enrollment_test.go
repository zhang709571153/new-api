package controller

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting"
	"github.com/QuantumNous/new-api/setting/ratio_setting"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Uses the existing TEST_WORKSPACE_DSN / TEST_WORKSPACE_LOG_DSN harness, so
// the same read-only reconciliation is exercised on real supported databases.
func TestSub2APIEnrollmentReconcilesCommittedWorkspaceSubjects(t *testing.T) {
	owner, member, team, _ := setupFundingIsolation(t)
	require.NoError(t, model.DB.AutoMigrate(&model.Channel{}, &model.Ability{}))
	baseURL := "http://127.0.0.1:29999"
	allowed := model.Channel{Name: "enrollment-allowed", Type: constant.ChannelTypeSub2API, Status: common.ChannelStatusEnabled, BaseURL: &baseURL, Key: "unused", Models: "allowed-model", Group: "default"}
	denied := model.Channel{Name: "enrollment-denied", Type: constant.ChannelTypeSub2API, Status: common.ChannelStatusEnabled, BaseURL: &baseURL, Key: "unused", Models: "private-model", Group: "private"}
	require.NoError(t, model.DB.Create(&allowed).Error)
	require.NoError(t, model.DB.Create(&denied).Error)
	require.NoError(t, model.DB.Create(&model.Ability{Group: "default", Model: "allowed-model", ChannelId: allowed.Id, Enabled: true}).Error)
	require.NoError(t, model.DB.Create(&model.Ability{Group: "private", Model: "private-model", ChannelId: denied.Id, Enabled: true}).Error)
	root := t.TempDir()
	queueDir, stateDir := filepath.Join(root, "queue"), filepath.Join(root, "state")
	require.NoError(t, os.MkdirAll(stateDir, 0700))
	t.Setenv("REALYU_UPSTREAM_DRIVER", "sub2api")
	t.Setenv("REALYU_SUB2API_QUEUE_DIR", queueDir)
	t.Setenv("REALYU_SUB2API_STATE_DIR", stateDir)
	cfg := service.Sub2APIConfig{Version: 1, Namespace: "enrollment-test", IdentitySecret: strings.Repeat("s", 32), AdminAPIKey: "unused-test-admin", Pools: []service.Sub2APIPool{{ChannelID: allowed.Id, GroupID: 10, BaseURL: baseURL}, {ChannelID: denied.Id, GroupID: 20, BaseURL: baseURL}}}
	cfg.Provision.Enabled, cfg.Provision.Mode, cfg.Provision.InitialBalance, cfg.Provision.Concurrency = true, "prewarmed", 100, 10
	raw, err := common.Marshal(cfg)
	require.NoError(t, err)
	configPath := filepath.Join(root, "bindings.json")
	require.NoError(t, os.WriteFile(configPath, raw, 0600))
	t.Setenv("REALYU_SUB2API_BINDINGS_FILE", configPath)
	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	jobDir := filepath.Join(queueDir, digest)
	assertJobs := func(want int) {
		t.Helper()
		jobs, err := filepath.Glob(filepath.Join(jobDir, "*.json"))
		require.NoError(t, err)
		require.Len(t, jobs, want)
		for _, path := range jobs {
			body, err := os.ReadFile(path)
			require.NoError(t, err)
			var job struct {
				ConfigSHA256 string `json:"config_sha256"`
				Task         struct {
					UserID    int `json:"realyu_user_id"`
					TeamID    int `json:"workspace_team_id"`
					ChannelID int `json:"channel_id"`
				} `json:"task"`
			}
			require.NoError(t, common.Unmarshal(body, &job))
			assert.Equal(t, digest, job.ConfigSHA256)
			assert.Equal(t, allowed.Id, job.Task.ChannelID, "private-group pool must never be enrolled")
		}
	}
	require.NoError(t, service.ReconcileSub2APIEnrollments(context.Background()))
	assertJobs(4) // owner/member each has separate personal and team identities.
	for _, userID := range []int{owner.Id, member.Id} {
		for _, teamID := range []int{0, team.ID} {
			_, err := os.Stat(filepath.Join(jobDir, fmt.Sprintf("%d-%d-%d.json", userID, teamID, allowed.Id)))
			require.NoError(t, err)
		}
	}
	require.NoError(t, service.ReconcileSub2APIEnrollments(context.Background()))
	assertJobs(4)
	newUser := model.User{Username: "enrollment-new", AffCode: "enrollment-new", Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1}
	require.NoError(t, model.DB.Create(&newUser).Error)
	// A legacy custom-group token is not the workspace's default personal
	// credential, and must not suppress preparation of its default route.
	require.NoError(t, model.DB.Create(&model.Token{UserId: newUser.Id, Key: "enrollment-custom-group", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, Group: "private"}).Error)
	disabled := model.User{Username: "enrollment-disabled", AffCode: "enrollment-disabled", Status: common.UserStatusDisabled, Group: "default", AuthVersion: 1}
	require.NoError(t, model.DB.Create(&disabled).Error)
	require.NoError(t, service.ReconcileSub2APIEnrollments(context.Background()))
	assertJobs(5)
	invite, _, err := model.CreateWorkspaceInvite(owner.Id)
	require.NoError(t, err)
	require.NoError(t, model.JoinWorkspaceTeam(newUser.Id, invite))
	require.NoError(t, service.ReconcileSub2APIEnrollments(context.Background()))
	assertJobs(6)
	heartbeat, err := common.Marshal(map[string]any{"version": 1, "config_sha256": digest, "last_seen": time.Now().UTC(), "status": "running"})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(stateDir, "heartbeat.json"), heartbeat, 0600))
	pending := service.GetSub2APIEnrollmentStatus(member.Id, "team")
	assert.Equal(t, "pending", pending.Status)
	assert.Equal(t, 1, pending.PendingCount)
	progress, err := common.Marshal(map[string]any{"version": 1, "config_sha256": digest, "tasks": map[string]any{fmt.Sprintf("%d:%d:%d", member.Id, team.ID, allowed.Id): map[string]any{"status": "DONE", "attempts": 1}}})
	require.NoError(t, err)
	statePath := filepath.Join(stateDir, digest+".json")
	require.NoError(t, os.WriteFile(statePath, progress, 0600))
	ready := service.GetSub2APIEnrollmentStatus(member.Id, "team")
	assert.Equal(t, "ready", ready.Status)
	assert.Equal(t, 1, ready.ReadyCount)
	creditBlocked, err := common.Marshal(map[string]any{"version": 1, "config_sha256": digest, "last_seen": time.Now().UTC(), "status": "idle", "credit_status": "blocked"})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(stateDir, "heartbeat.json"), creditBlocked, 0600))
	creditStatus := service.GetSub2APIEnrollmentStatus(member.Id, "team")
	assert.Equal(t, "error", creditStatus.Status, "ready identities must not hide blocked internal-credit maintenance")
	assert.Equal(t, "operator_action_required", creditStatus.Reason)
	assert.Equal(t, 1, creditStatus.ReadyCount)
	require.NoError(t, os.WriteFile(filepath.Join(stateDir, "heartbeat.json"), heartbeat, 0600))
	assert.Equal(t, "pending", service.GetSub2APIEnrollmentStatus(member.Id, "personal").Status)
	after, err := os.ReadFile(statePath)
	require.NoError(t, err)
	assert.Equal(t, progress, after, "web status must not write worker progress")

	// Existing native API keys are part of personal readiness, even when the
	// standard workspace key is already prepared. Group and model permissions
	// remain per key before taking the subject's union of authorized pools.
	originalGroups := setting.UserUsableGroups2JSONString()
	originalRatios := ratio_setting.GroupRatio2JSONString()
	t.Cleanup(func() {
		require.NoError(t, setting.UpdateUserUsableGroupsByJSONString(originalGroups))
		require.NoError(t, ratio_setting.UpdateGroupRatioByJSONString(originalRatios))
	})
	require.NoError(t, setting.UpdateUserUsableGroupsByJSONString(`{"default":"Default","vip":"VIP","auto":"Auto"}`))
	require.NoError(t, ratio_setting.UpdateGroupRatioByJSONString(`{"default":1,"vip":1}`))
	vipChannels := []model.Channel{
		{Name: "enrollment-native-a", Type: constant.ChannelTypeSub2API, Status: common.ChannelStatusEnabled, BaseURL: &baseURL, Key: "unused", Models: "native-a", Group: "vip"},
		{Name: "enrollment-native-b", Type: constant.ChannelTypeSub2API, Status: common.ChannelStatusEnabled, BaseURL: &baseURL, Key: "unused", Models: "native-b", Group: "vip"},
	}
	for i := range vipChannels {
		require.NoError(t, model.DB.Create(&vipChannels[i]).Error)
		require.NoError(t, model.DB.Create(&model.Ability{Group: "vip", Model: vipChannels[i].Models, ChannelId: vipChannels[i].Id, Enabled: true}).Error)
		cfg.Pools = append(cfg.Pools, service.Sub2APIPool{ChannelID: vipChannels[i].Id, GroupID: 30 + i, BaseURL: baseURL})
	}
	raw, err = common.Marshal(cfg)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(configPath, raw, 0600))
	sum = sha256.Sum256(raw)
	digest = hex.EncodeToString(sum[:])
	jobDir = filepath.Join(queueDir, digest)
	for _, token := range []model.Token{
		{UserId: member.Id, Key: "enrollment-native-valid", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, Group: "vip", ModelLimitsEnabled: true, ModelLimits: "native-a"},
		{UserId: member.Id, Key: "enrollment-native-disabled", Status: common.TokenStatusDisabled, UnlimitedQuota: true, ExpiredTime: -1, Group: "vip", ModelLimitsEnabled: true, ModelLimits: "native-b"},
		{UserId: member.Id, Key: "enrollment-native-expired", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: time.Now().Unix() - 60, Group: "vip", ModelLimitsEnabled: true, ModelLimits: "native-b"},
		{UserId: member.Id, Key: "enrollment-native-forbidden-auto", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, Group: "auto", AutoGroups: `["private"]`},
	} {
		require.NoError(t, model.DB.Create(&token).Error)
	}
	require.NoError(t, service.ReconcileSub2APIEnrollments(context.Background()))
	jobPath := func(teamID, channelID int) string {
		return filepath.Join(jobDir, fmt.Sprintf("%d-%d-%d.json", member.Id, teamID, channelID))
	}
	require.FileExists(t, jobPath(0, vipChannels[0].Id))
	require.NoFileExists(t, jobPath(0, vipChannels[1].Id), "disabled, expired and model-excluded keys grant no extra route")
	require.NoFileExists(t, jobPath(0, denied.Id), "auto must filter revoked groups")
	require.NoFileExists(t, jobPath(team.ID, vipChannels[0].Id), "personal permissions must not become team access")
	progress, err = common.Marshal(map[string]any{"version": 1, "config_sha256": digest, "tasks": map[string]any{fmt.Sprintf("%d:0:%d", member.Id, allowed.Id): map[string]any{"status": "DONE"}}})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(stateDir, digest+".json"), progress, 0600))
	heartbeat, err = common.Marshal(map[string]any{"version": 1, "config_sha256": digest, "last_seen": time.Now().UTC(), "status": "running"})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(stateDir, "heartbeat.json"), heartbeat, 0600))
	personal := service.GetSub2APIEnrollmentStatus(member.Id, "personal")
	assert.Equal(t, "pending", personal.Status, "a prepared default key cannot hide an unprepared native key pool")
	assert.Equal(t, 1, personal.ReadyCount)
	assert.Equal(t, 1, personal.PendingCount)
	require.NoError(t, model.DB.Create(&model.Token{UserId: member.Id, Key: "enrollment-native-new-auto", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, Group: "auto", AutoGroups: `["private","vip"]`, ModelLimitsEnabled: true, ModelLimits: "native-b"}).Error)
	require.NoError(t, service.ReconcileSub2APIEnrollments(context.Background()))
	require.FileExists(t, jobPath(0, vipChannels[1].Id), "new keys are included on the next scan")
	assert.Equal(t, 2, service.GetSub2APIEnrollmentStatus(member.Id, "personal").PendingCount)
	require.NoError(t, setting.UpdateUserUsableGroupsByJSONString(`{"default":"Default","auto":"Auto"}`))
	assert.Equal(t, "ready", service.GetSub2APIEnrollmentStatus(member.Id, "personal").Status, "revoked group access is not counted despite historical jobs")
	// Existing customer money remains untouched by scans, enqueue and status.
	var ownerAfter, memberAfter model.User
	require.NoError(t, model.DB.First(&ownerAfter, owner.Id).Error)
	require.NoError(t, model.DB.First(&memberAfter, member.Id).Error)
	assert.Equal(t, owner.Quota, ownerAfter.Quota)
	assert.Equal(t, member.Quota, memberAfter.Quota)
	assert.Zero(t, ownerAfter.UsedQuota)
	assert.Zero(t, memberAfter.UsedQuota)
}
