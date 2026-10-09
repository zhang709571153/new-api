package sub2api

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/relay/channel"
	"github.com/QuantumNous/new-api/relay/channel/newapi"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/tidwall/gjson"
	"github.com/tidwall/sjson"
)

type Adaptor struct {
	newapi.Adaptor
}

func (a *Adaptor) SetupRequestHeader(c *gin.Context, headers *http.Header, info *relaycommon.RelayInfo) error {
	if err := a.Adaptor.SetupRequestHeader(c, headers, info); err != nil {
		return err
	}
	if !service.Sub2APIDriverEnabled() {
		return nil
	}
	if sub2APIUsesJSON(c, info) {
		headers.Set("Content-Type", "application/json")
	}
	for _, name := range []string{"Originator", "User-Agent", "OpenAI-Beta", "X-Codex-Beta-Features", "X-Codex-Turn-State", "X-OpenAI-Subagent", "X-OpenAI-Memgen-Request", "X-ResponsesAPI-Include-Timing-Metrics", "X-OpenAI-Internal-Codex-Responses-Lite"} {
		if value := c.GetHeader(name); value != "" {
			headers.Set(name, value)
		}
	}
	if metadata := c.GetHeader("X-Codex-Turn-Metadata"); metadata != "" {
		scoped, err := service.Sub2APIScopeTurnMetadata(service.Sub2APISubjectFromContext(c), metadata)
		if err != nil {
			return err
		}
		headers.Set("X-Codex-Turn-Metadata", scoped)
	}
	for _, name := range []string{"Session_id", "Session-Id", "Thread_id", "Thread-Id", "X-Session-ID", "X-Session-Affinity", "Conversation_id", "X-Conversation-ID", "X-Opencode-Session", "X-Codex-Window-Id", "X-Codex-Parent-Thread-Id"} {
		if value := c.GetHeader(name); value != "" {
			scoped, err := service.Sub2APISessionID(service.Sub2APISubjectFromContext(c), value)
			if err != nil {
				return err
			}
			headers.Set(name, scoped)
		}
	}
	if headers.Get("Session_id") == "" {
		for _, name := range []string{"Session-Id", "Thread_id", "Thread-Id", "Conversation_id", "X-Session-ID", "X-Session-Affinity", "X-Conversation-ID", "X-Opencode-Session"} {
			if scoped := headers.Get(name); scoped != "" {
				headers.Set("Session_id", scoped)
				break
			}
		}
		if headers.Get("Session_id") == "" {
			storage, err := common.GetBodyStorage(c)
			if err != nil {
				return errors.New("Sub2API session request is unavailable")
			}
			raw, err := storage.Bytes()
			if err != nil {
				return errors.New("Sub2API session request is unavailable")
			}
			signal := gjson.GetBytes(raw, "prompt_cache_key").String()
			if signal == "" {
				sum := sha256.Sum256(raw)
				signal = "request:" + hex.EncodeToString(sum[:])
			}
			scoped, err := service.Sub2APISessionID(service.Sub2APISubjectFromContext(c), signal)
			if err != nil {
				return err
			}
			headers.Set("Session_id", scoped)
		}
	}
	if requestID := c.GetString(common.RequestIdKey); requestID != "" {
		headers.Set("X-Client-Request-ID", requestID)
	}
	return nil
}

func (a *Adaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, body io.Reader) (any, error) {
	if service.Sub2APIDriverEnabled() && sub2APIUsesJSON(c, info) {
		data, err := io.ReadAll(body)
		if err != nil {
			return nil, errors.New("failed to read Sub2API request")
		}
		// Scope only cache/session identity; retain every other field, including
		// unknown Codex extensions, explicit zero values and encrypted reasoning.
		data, err = service.Sub2APIScopeRequestBody(service.Sub2APISubjectFromContext(c), data)
		if err != nil {
			return nil, err
		}
		if info.IsStream && strings.HasSuffix(info.RequestURLPath, "/chat/completions") {
			// The outer OpenAI handler filters this usage chunk according to the
			// customer's original option; accounting still needs actual usage.
			data, err = sjson.SetBytes(data, "stream_options.include_usage", true)
			if err != nil {
				return nil, errors.New("invalid Sub2API stream options")
			}
		}
		body = bytes.NewReader(data)
	}
	resp, err := channel.DoApiRequest(a, c, info, body)
	if resp != nil && resp.Header.Get("X-Client-Request-ID") != "" {
		c.Set(common.UpstreamRequestIdKey, resp.Header.Get("X-Client-Request-ID"))
	}
	return resp, err
}

func sub2APIUsesJSON(c *gin.Context, info *relaycommon.RelayInfo) bool {
	// Only validated image-edit multipart requests carry a binary body. A
	// client's missing/mixed-case Content-Type cannot opt out of tenant scope.
	return info.RelayMode != relayconstant.RelayModeImagesEdits || !strings.HasPrefix(strings.ToLower(c.GetHeader("Content-Type")), "multipart/form-data")
}

func (a *Adaptor) GetModelList() []string {
	return ModelList
}

func (a *Adaptor) GetChannelName() string {
	return ChannelName
}
