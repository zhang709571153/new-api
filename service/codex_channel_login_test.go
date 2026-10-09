package service

import (
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/mysql"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

type codexLoginTransport func(*http.Request) (*http.Response, error)

func (f codexLoginTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestCodexChannelLogin(t *testing.T) {
	engine := os.Getenv("TEST_LOGIN_DB_ENGINE")
	var dialect gorm.Dialector = sqlite.Open(":memory:")
	if engine == "mysql" {
		dialect = mysql.Open(os.Getenv("TEST_LOGIN_DSN"))
	}
	if engine == "postgres" {
		dialect = postgres.Open(os.Getenv("TEST_LOGIN_DSN"))
	}
	db, err := gorm.Open(dialect, &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	defer sqlDB.Close()
	require.NoError(t, db.AutoMigrate(&model.Channel{}, &model.Ability{}))
	previousDB, previousClient, previousCache := model.DB, httpClient, common.MemoryCacheEnabled
	model.DB, common.MemoryCacheEnabled = db, false
	t.Cleanup(func() { model.DB, httpClient, common.MemoryCacheEnabled = previousDB, previousClient, previousCache })
	identity := AuthIdentity{UserID: 4, SessionID: "initiating-session"}
	ch := &model.Channel{Type: constant.ChannelTypeCodex, Key: `{"account_id":"account-A","access_token":"old"}`, Name: "Pro A", Status: 3, Weight: common.GetPointer(uint(400)), Models: "gpt-6-luna", Group: "default"}
	require.NoError(t, db.Create(ch).Error)
	status := http.StatusOK
	httpClient = &http.Client{Transport: codexLoginTransport(func(r *http.Request) (*http.Response, error) {
		assert.Equal(t, "https://chatgpt.com/backend-api/wham/usage", r.URL.String())
		assert.Equal(t, "account-A", r.Header.Get("ChatGPT-Account-ID"))
		return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(`{"rate_limit":{"allowed":true}}`)), Header: make(http.Header)}, nil
	})}
	newAttempt := func() *codexChannelLogin {
		ctx, cancel := context.WithCancel(context.Background())
		_ = ctx
		a := &codexChannelLogin{userID: identity.UserID, sessionID: identity.SessionID, channelID: ch.Id, accountID: "account-A", cancel: cancel,
			view: CodexChannelLoginView{ID: "test-attempt", Status: "pending", ExpiresAt: time.Now().Add(time.Minute).Unix()}}
		codexChannelLogins.items[a.view.ID] = a
		t.Cleanup(func() { cancel(); delete(codexChannelLogins.items, a.view.ID) })
		return a
	}
	credential := func(account string) []byte {
		claims, e := common.Marshal(map[string]any{codexJWTClaimPath: map[string]string{"chatgpt_account_id": account}, "email": "pro@example.test"})
		require.NoError(t, e)
		data, e := common.Marshal(map[string]any{"tokens": CodexOAuthKey{AccountID: account, AccessToken: "e30." + base64.RawURLEncoding.EncodeToString(claims) + ".signature", RefreshToken: "fixture-refresh"}})
		require.NoError(t, e)
		return data
	}
	t.Run("wrong account cannot replace a channel", func(t *testing.T) {
		a := newAttempt()
		a.acceptCredential(credential("account-B"))
		assert.Equal(t, "failed", a.view.Status)
		assert.Equal(t, "account_mismatch", a.view.ErrorCode)
		assert.Nil(t, a.credential)
		assert.Error(t, CompleteCodexChannelLogin(context.Background(), identity, ch.Id, a.view.ID))
	})
	t.Run("attempt is bound to channel user and session", func(t *testing.T) {
		a := newAttempt()
		a.acceptCredential(credential("account-A"))
		for _, wrong := range []AuthIdentity{{UserID: 5, SessionID: identity.SessionID}, {UserID: 4, SessionID: "other"}, {UserID: 4}} {
			_, e := GetCodexChannelLogin(wrong, ch.Id, a.view.ID)
			assert.Error(t, e)
			assert.Error(t, CompleteCodexChannelLogin(context.Background(), wrong, ch.Id, a.view.ID))
			assert.Error(t, CancelCodexChannelLogin(wrong, ch.Id, a.view.ID))
		}
		_, e := GetCodexChannelLogin(identity, ch.Id+1, a.view.ID)
		assert.Error(t, e)
	})
	t.Run("expired and cancelled attempts cannot be consumed", func(t *testing.T) {
		a := newAttempt()
		a.acceptCredential(credential("account-A"))
		a.view.ExpiresAt = time.Now().Add(-time.Minute).Unix()
		assert.Error(t, CompleteCodexChannelLogin(context.Background(), identity, ch.Id, a.view.ID))
		v, e := GetCodexChannelLogin(identity, ch.Id, a.view.ID)
		require.NoError(t, e)
		assert.Equal(t, "login_expired", v.ErrorCode)
		assert.Nil(t, a.credential)
		a = newAttempt()
		a.acceptCredential(credential("account-A"))
		require.NoError(t, CancelCodexChannelLogin(identity, ch.Id, a.view.ID))
		assert.Nil(t, a.credential)
		assert.Error(t, CompleteCodexChannelLogin(context.Background(), identity, ch.Id, a.view.ID))
	})
	t.Run("upstream rejection preserves the old credential and successful claim is single use", func(t *testing.T) {
		a := newAttempt()
		a.acceptCredential(credential("account-A"))
		status = 401
		assert.Error(t, CompleteCodexChannelLogin(context.Background(), identity, ch.Id, a.view.ID))
		var loaded model.Channel
		require.NoError(t, db.First(&loaded, ch.Id).Error)
		assert.Equal(t, ch.Key, loaded.Key)
		status = 200
		require.NoError(t, CompleteCodexChannelLogin(context.Background(), identity, ch.Id, a.view.ID))
		require.NoError(t, db.First(&loaded, ch.Id).Error)
		assert.NotEqual(t, ch.Key, loaded.Key)
		assert.Equal(t, ch.Status, loaded.Status)
		assert.Equal(t, ch.Weight, loaded.Weight)
		assert.Equal(t, ch.Models, loaded.Models)
		assert.Nil(t, a.credential)
		assert.Error(t, CompleteCodexChannelLogin(context.Background(), identity, ch.Id, a.view.ID))
		encoded, e := common.Marshal(a.view)
		require.NoError(t, e)
		assert.NotContains(t, string(encoded), "fixture-refresh")
		assert.NotContains(t, string(encoded), "access_token")
	})
	for _, notification := range []bool{false, true} {
		t.Run(fmt.Sprintf("persisted official grant is accepted with completion notification %t", notification), func(t *testing.T) {
			a := newAttempt()
			directory := t.TempDir()
			input, output := io.Pipe()
			defer input.Close()
			defer output.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			stopped := false
			send := func(value any) error {
				data, e := common.Marshal(value)
				require.NoError(t, e)
				if strings.Contains(string(data), "account/login/start") {
					require.NoError(t, os.WriteFile(filepath.Join(directory, "auth.json"), credential("account-A"), 0600))
				}
				return nil
			}
			go func() {
				_, _ = io.WriteString(output, `{"id":1,"result":{}}`+"\n")
				if notification {
					_, _ = io.WriteString(output, `{"method":"account/login/completed","params":{"success":true}}`+"\n")
				}
			}()
			a.receiveLogin(ctx, input, send, directory, func() { stopped = true })
			require.NoError(t, ctx.Err())
			assert.True(t, stopped, "the temporary refresh owner must stop before the grant is accepted")
			assert.Equal(t, "ready", a.view.Status)
			assert.NotNil(t, a.credential)
		})
	}
}
