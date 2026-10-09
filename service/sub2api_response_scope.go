package service

import (
	"crypto/hmac"
	"encoding/base64"
	"errors"
	"strings"

	"github.com/tidwall/gjson"
	"github.com/tidwall/sjson"
)

const sub2APIResponsePrefix = "resp_ry1_"

var ErrSub2APIResponseNotOwned = errors.New("response reference is not available to this user and workspace")

// Response IDs are opaque to clients. Authenticate their member/workspace scope
// before forwarding a retained-response reference to the shared upstream pool.
// Unlike an in-memory owner cache, this survives gateway restarts and scales
// across hosts sharing the existing immutable namespace and identity secret.
func Sub2APIWrapResponseID(subject Sub2APISubject, id string) (string, error) {
	if subject.UserID <= 0 || subject.WorkspaceTeamID < 0 || len(id) > 512 || !strings.HasPrefix(id, "resp_") {
		return "", ErrSub2APIResponseNotOwned
	}
	cfg, err := LoadSub2APIConfig()
	if err != nil {
		return "", err
	}
	encoded := base64.RawURLEncoding.EncodeToString([]byte(id))
	return sub2APIResponsePrefix + encoded + "." + sub2APIIdentity(cfg, "response:"+id, subject, 0), nil
}

func Sub2APIUnwrapResponseID(subject Sub2APISubject, id string) (string, error) {
	if len(id) > 800 || !strings.HasPrefix(id, sub2APIResponsePrefix) {
		return "", ErrSub2APIResponseNotOwned
	}
	encoded, signature, ok := strings.Cut(strings.TrimPrefix(id, sub2APIResponsePrefix), ".")
	if !ok || len(signature) != 64 {
		return "", ErrSub2APIResponseNotOwned
	}
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil || base64.RawURLEncoding.EncodeToString(raw) != encoded {
		return "", ErrSub2APIResponseNotOwned
	}
	expected, err := Sub2APIWrapResponseID(subject, string(raw))
	if err != nil {
		return "", err
	}
	if !hmac.Equal([]byte(expected), []byte(id)) {
		return "", ErrSub2APIResponseNotOwned
	}
	return string(raw), nil
}

func Sub2APIUnscopeResponseReference(subject Sub2APISubject, body []byte, field string) ([]byte, error) {
	value := gjson.GetBytes(body, field)
	if !value.Exists() || value.Type == gjson.Null {
		return body, nil
	}
	if value.Type != gjson.String {
		return nil, ErrSub2APIResponseNotOwned
	}
	if value.String() == "" {
		return body, nil
	}
	id, err := Sub2APIUnwrapResponseID(subject, value.String())
	if err != nil {
		return nil, err
	}
	return sjson.SetBytes(body, field, id)
}

// Touch only protocol response identifiers. Preserve item IDs, text, encrypted
// reasoning, usage, unknown extensions, explicit zero values and JSON numbers.
func Sub2APIScopeResponseBody(subject Sub2APISubject, body []byte) ([]byte, error) {
	for _, field := range []string{"id", "response.id", "response_id", "previous_response_id", "response.previous_response_id"} {
		value := gjson.GetBytes(body, field)
		if value.Type != gjson.String || !strings.HasPrefix(value.String(), "resp_") {
			continue
		}
		id, err := Sub2APIWrapResponseID(subject, value.String())
		if err != nil {
			return nil, err
		}
		body, err = sjson.SetBytes(body, field, id)
		if err != nil {
			return nil, err
		}
	}
	return body, nil
}
