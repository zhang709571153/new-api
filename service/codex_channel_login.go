package service

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
)

// OAuth is performed by the official Codex app-server in a disposable home.
// Only the initiating dashboard session can claim the grant. Credentials never
// leave the server, and an existing channel can only reconnect the same account.
type CodexChannelLoginView struct {
	ID              string `json:"id"`
	Status          string `json:"status"`
	Email           string `json:"email"`
	VerificationURL string `json:"verification_url,omitempty"`
	UserCode        string `json:"user_code,omitempty"`
	ExpiresAt       int64  `json:"expires_at"`
	ErrorCode       string `json:"error_code,omitempty"`
}

type codexChannelLogin struct {
	mu                   sync.Mutex
	view                 CodexChannelLoginView
	userID, channelID    int
	sessionID, accountID string
	credential           *CodexOAuthKey
	cancel               context.CancelFunc
}

var codexChannelLogins = struct {
	sync.Mutex
	items map[string]*codexChannelLogin
}{items: make(map[string]*codexChannelLogin)}

var ErrCodexChannelLogin = errors.New("Channel login is unavailable. Please start again.")

func StartCodexChannelLogin(identity AuthIdentity, channelID int) (*CodexChannelLoginView, error) {
	ch, err := model.GetChannelById(channelID, true)
	if err != nil || ch == nil || ch.Type != constant.ChannelTypeCodex || ch.ChannelInfo.IsMultiKey || identity.SessionID == "" {
		return nil, ErrCodexChannelLogin
	}
	key, err := parseCodexOAuthKey(ch.Key)
	if err != nil || key.AccountID == "" {
		return nil, ErrCodexChannelLogin
	}
	executable := os.Getenv("CODEX_CHANNEL_LOGIN_EXE")
	if !filepath.IsAbs(executable) {
		return nil, ErrCodexChannelLogin
	}
	root := os.Getenv("CODEX_CHANNEL_LOGIN_HOME")
	if !filepath.IsAbs(root) {
		return nil, ErrCodexChannelLogin
	}
	codexChannelLogins.Lock()
	defer codexChannelLogins.Unlock()
	for id, attempt := range codexChannelLogins.items {
		attempt.mu.Lock()
		if attempt.view.ExpiresAt <= time.Now().Unix() || attempt.view.Status == "completed" || attempt.view.Status == "failed" || attempt.view.Status == "cancelled" {
			attempt.cancel()
			attempt.credential = nil
			delete(codexChannelLogins.items, id)
		} else if attempt.channelID == channelID {
			attempt.mu.Unlock()
			return nil, errors.New("A channel login is already in progress. Cancel it or wait for it to expire.")
		}
		attempt.mu.Unlock()
	}
	if len(codexChannelLogins.items) >= 2 {
		return nil, ErrCodexChannelLogin
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, ErrCodexChannelLogin
	}
	directory, err := os.MkdirTemp(root, "login-")
	if err != nil {
		return nil, ErrCodexChannelLogin
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	a := &codexChannelLogin{userID: identity.UserID, sessionID: identity.SessionID, channelID: channelID,
		accountID: key.AccountID, cancel: cancel,
		view: CodexChannelLoginView{ID: uuid.NewString(), Status: "starting", Email: key.Email, ExpiresAt: time.Now().Add(10 * time.Minute).Unix()}}
	codexChannelLogins.items[a.view.ID] = a
	view := a.view
	go a.run(ctx, executable, directory)
	time.AfterFunc(10*time.Minute, func() {
		a.mu.Lock()
		a.cancel()
		a.credential = nil
		a.view.UserCode = ""
		a.mu.Unlock()
		codexChannelLogins.Lock()
		delete(codexChannelLogins.items, a.view.ID)
		codexChannelLogins.Unlock()
	})
	return &view, nil
}

func (a *codexChannelLogin) run(ctx context.Context, executable, directory string) {
	defer os.RemoveAll(directory)
	defer a.cancel()
	defer func() {
		a.mu.Lock()
		defer a.mu.Unlock()
		if a.view.Status == "starting" || a.view.Status == "pending" {
			a.view.Status, a.view.ErrorCode = "failed", "login_failed"
			a.view.UserCode, a.view.VerificationURL = "", ""
			if ctx.Err() != nil {
				a.view.ErrorCode = "login_expired"
			}
		}
		common.SysLog(fmt.Sprintf("codex_channel_login channel_id=%d state=%s code=%s", a.channelID, a.view.Status, a.view.ErrorCode))
	}()
	cmd := exec.CommandContext(ctx, executable, "-c", `cli_auth_credentials_store="file"`, "app-server")
	configureCodexLoginProcess(cmd)
	cmd.Dir = directory
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		switch strings.ToUpper(name) {
		case "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME", "LANG", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE":
			cmd.Env = append(cmd.Env, entry)
		}
	}
	cmd.Env = append(cmd.Env, "CODEX_HOME="+directory)
	cmd.Stderr = io.Discard
	in, err := cmd.StdinPipe()
	if err != nil {
		return
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return
	}
	if err = cmd.Start(); err != nil {
		return
	}
	defer func() { _ = in.Close(); _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	send := func(value any) error {
		data, err := common.Marshal(value)
		if err != nil {
			return err
		}
		_, err = in.Write(append(data, '\n'))
		return err
	}
	if send(map[string]any{"id": 1, "method": "initialize", "params": map[string]any{
		"clientInfo":   map[string]string{"name": "realyu-channel-login", "version": "1.0"},
		"capabilities": map[string]bool{"experimentalApi": true},
	}}) != nil {
		return
	}
	a.receiveLogin(ctx, out, send, directory, func() { _ = in.Close(); _ = cmd.Process.Kill(); _ = cmd.Wait() })
}

// Some app-server versions persist auth before sending the completion notification.
// Observe both signals, and stop the temporary refresh owner before accepting tokens.
func (a *codexChannelLogin) receiveLogin(ctx context.Context, out io.Reader, send func(any) error, directory string, stop func()) {
	messages := make(chan []byte)
	readCtx, readCancel := context.WithCancel(ctx)
	defer readCancel()
	go func() {
		defer close(messages)
		scanner := bufio.NewScanner(out)
		scanner.Buffer(make([]byte, 4096), 1024*1024)
		for scanner.Scan() {
			line := append([]byte(nil), scanner.Bytes()...)
			select {
			case messages <- line:
			case <-readCtx.Done():
				return
			}
		}
	}()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	var completedAt time.Time
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		case line, open := <-messages:
			if !open {
				messages = nil
			}
			if open {
				var message struct {
					ID     int    `json:"id"`
					Method string `json:"method"`
					Error  any    `json:"error"`
					Result struct {
						Type            string `json:"type"`
						VerificationURL string `json:"verificationUrl"`
						UserCode        string `json:"userCode"`
					} `json:"result"`
					Params struct {
						Success bool   `json:"success"`
						Error   string `json:"error"`
					} `json:"params"`
				}
				if common.Unmarshal(line, &message) != nil {
					continue
				}
				if message.Error != nil {
					return
				}
				if message.ID == 1 {
					if send(map[string]any{"method": "initialized"}) != nil {
						return
					}
					if send(map[string]any{"id": 2, "method": "account/login/start", "params": map[string]string{"type": "chatgptDeviceCode"}}) != nil {
						return
					}
				}
				if message.ID == 2 {
					if message.Result.Type != "chatgptDeviceCode" || message.Result.VerificationURL != "https://auth.openai.com/codex/device" || len(message.Result.UserCode) > 32 || message.Result.UserCode == "" {
						return
					}
					a.mu.Lock()
					a.view.Status, a.view.VerificationURL, a.view.UserCode = "pending", message.Result.VerificationURL, message.Result.UserCode
					a.mu.Unlock()
				}
				if message.Method == "account/login/completed" {
					if !message.Params.Success {
						code := "upstream_login_failed"
						if strings.Contains(message.Params.Error, "exchange") {
							code = "token_exchange_failed"
						}
						a.mu.Lock()
						a.view.Status, a.view.ErrorCode = "failed", code
						a.view.UserCode, a.view.VerificationURL = "", ""
						a.mu.Unlock()
						return
					}
					completedAt = time.Now()
				}
			}
		}
		file, err := os.Open(filepath.Join(directory, "auth.json"))
		if err == nil {
			data, readErr := io.ReadAll(io.LimitReader(file, 128*1024+1))
			_ = file.Close()
			var auth struct {
				Tokens CodexOAuthKey `json:"tokens"`
			}
			if readErr == nil && len(data) <= 128*1024 && common.Unmarshal(data, &auth) == nil && auth.Tokens.AccessToken != "" && auth.Tokens.RefreshToken != "" {
				stop()
				a.mu.Lock()
				if ctx.Err() == nil && a.view.Status != "cancelled" {
					a.acceptCredential(data)
				}
				a.mu.Unlock()
				return
			}
		}
		if messages == nil || (!completedAt.IsZero() && time.Since(completedAt) > 5*time.Second) {
			return
		}
	}
}

func codexChannelLoginFor(identity AuthIdentity, channelID int, id string) (*codexChannelLogin, error) {
	codexChannelLogins.Lock()
	a := codexChannelLogins.items[id]
	codexChannelLogins.Unlock()
	if a == nil || identity.SessionID == "" || a.userID != identity.UserID || a.sessionID != identity.SessionID || a.channelID != channelID {
		return nil, ErrCodexChannelLogin
	}
	return a, nil
}

func GetCodexChannelLogin(identity AuthIdentity, channelID int, id string) (*CodexChannelLoginView, error) {
	a, err := codexChannelLoginFor(identity, channelID, id)
	if err != nil {
		return nil, err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.view.ExpiresAt <= time.Now().Unix() {
		a.cancel()
		a.credential = nil
		a.view.Status, a.view.ErrorCode = "failed", "login_expired"
		a.view.UserCode, a.view.VerificationURL = "", ""
	}
	view := a.view
	return &view, nil
}

func CancelCodexChannelLogin(identity AuthIdentity, channelID int, id string) error {
	a, err := codexChannelLoginFor(identity, channelID, id)
	if err != nil {
		return err
	}
	a.mu.Lock()
	a.cancel()
	a.credential = nil
	a.view.Status, a.view.UserCode, a.view.VerificationURL = "cancelled", "", ""
	a.mu.Unlock()
	codexChannelLogins.Lock()
	delete(codexChannelLogins.items, id)
	codexChannelLogins.Unlock()
	return nil
}

func CompleteCodexChannelLogin(ctx context.Context, identity AuthIdentity, channelID int, id string) error {
	a, err := codexChannelLoginFor(identity, channelID, id)
	if err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.view.Status != "ready" || a.credential == nil || a.view.ExpiresAt <= time.Now().Unix() {
		return ErrCodexChannelLogin
	}
	ch, err := model.GetChannelById(channelID, true)
	if err != nil || ch.Type != constant.ChannelTypeCodex || ch.ChannelInfo.IsMultiKey {
		return ErrCodexChannelLogin
	}
	previous, err := parseCodexOAuthKey(ch.Key)
	if err != nil || previous.AccountID != a.accountID {
		return ErrCodexChannelLogin
	}
	client, err := GetHttpClientWithProxy(ch.GetSetting().Proxy)
	if err != nil {
		return ErrCodexChannelLogin
	}
	checkCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	// Fixed official origin: a configured channel base URL must not receive a fresh grant.
	status, _, err := FetchCodexWhamUsage(checkCtx, client, "https://chatgpt.com", a.credential.AccessToken, a.accountID)
	if err != nil || status != http.StatusOK {
		return errors.New("The new credential could not be verified. Please retry.")
	}
	encoded, err := common.Marshal(a.credential)
	if err != nil {
		return err
	}
	result := model.DB.Model(&model.Channel{}).Where(map[string]any{"id": channelID, "type": constant.ChannelTypeCodex, "key": ch.Key}).Update("key", string(encoded))
	if result.Error != nil || result.RowsAffected != 1 {
		return ErrCodexChannelLogin
	}
	a.credential = nil
	a.view.Status = "completed"
	model.InitChannelCache()
	return nil
}

func (a *codexChannelLogin) acceptCredential(data []byte) {
	a.view.UserCode, a.view.VerificationURL = "", ""
	var auth struct {
		Tokens CodexOAuthKey `json:"tokens"`
	}
	if common.Unmarshal(data, &auth) != nil {
		a.view.Status, a.view.ErrorCode = "failed", "login_failed"
		return
	}
	key := &auth.Tokens
	accountID, ok := ExtractCodexAccountIDFromJWT(key.AccessToken)
	if !ok || accountID != a.accountID || key.AccountID != a.accountID || key.RefreshToken == "" {
		a.view.Status, a.view.ErrorCode = "failed", "account_mismatch"
		return
	}
	key.Email, _ = ExtractEmailFromJWT(key.AccessToken)
	key.Type, key.LastRefresh = "codex", time.Now().Format(time.RFC3339)
	a.credential, a.view.Status = key, "ready"
	a.view.UserCode, a.view.VerificationURL = "", ""
}
