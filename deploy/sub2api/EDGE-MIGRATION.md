# Existing Windows bridge replacement

`edge_proxy.py` replaces the legacy Images-to-Responses conversion with a
transport proxy. New API performs authentication, tenant isolation, image
handling, and billing. The proxy does not own OAuth credentials and does not
retry inference requests. This entry is only for the existing loopback bridge
topology; a new installation can put its reverse proxy directly before New API.

The complete runtime is three Python standard-library files in one directory:

- `edge_proxy.py` (may be installed as the existing `lab/image_bridge.py`).
- `release_control.py` (unchanged compatibility implementation).
- `bridge_observability.py` (unchanged metadata-only, bounded log implementation).

Run `python -m unittest -v test_edge_proxy` from this directory before packaging.
Six tests use real loopback sockets to cover HTTP JSON and multipart byte
preservation, request chunking, live SSE delivery, pipelined and large WebSocket
frames, fragmentation, ping/close, complete authentication errors, and admission
draining. These transport tests do not prove an upstream model or public tunnel
is working; the release acceptance suite must also run through the edge port.

## Runtime contract

| Environment | Existing host value / meaning |
| --- | --- |
| `REALYU_UPSTREAM_DRIVER` | `sub2api`, mandatory on both API and bridge services |
| `NEWAPI_PORT` | `18300`, loopback New API |
| `BRIDGE_PORT` | `18301`, loopback public-tunnel destination |
| `REALYU_MAINTENANCE_PORT` | `18302`, defaults to bridge port plus one |
| `REALYU_PRIVATE_DIR` | Existing protected private directory |

The gate remains `<REALYU_PRIVATE_DIR>/release-maintenance.json`. Its presence
rejects new HTTP and WebSocket requests with HTTP 503 and `Retry-After: 30`.
Existing requests, including WebSockets, stay counted until they end. The
maintenance endpoint remains `{maintenance, active_requests, pid, protocol,
observation}`. It is not a remote administration interface. The proxy binds
only `127.0.0.1`. HTTP bodies are bounded to 128 MiB; transport idle timeouts are
300 seconds. The gateway continues enforcing its own route and request limits.

All ordinary paths, including `/v1/images/generations` and `/v1/images/edits`,
are forwarded to New API without model rewriting. Only the Responses
`GET /v1/responses` path allows WebSocket upgrade. Authenticated upgrade errors
retain their status and complete body; successful upgrades relay both directions
without inspecting or logging frames. Metadata logs never include authorization
headers, bodies, response content, or arbitrary URL queries.

## Cutover and rollback constraints

The operator must first validate the complete candidate behind distinct loopback
ports using a cloned customer database and preserve the existing production
state. The existing Windows host uses SCM services; the historical scheduled
task is not the active owner. Capture and back up the actual service launcher,
service XML, bridge script and three runtime files before replacement.

Prepare a disabled type 59 route through the legacy management API, retaining
the existing downstream group and model identifiers. Keep old channel rows for
history; ordinary customer tokens reference groups and models, so channel IDs
need not be reused. An administrator using a key suffix that pins an old channel
must update that pin. Do not alter customer keys or financial records.

Close admission and verify `active_requests == 0` before changing routing or
stopping the old gateway. Long-lived WebSockets may require a bounded, announced
disconnect. Use the official `POST /api/channel/:id/status` endpoint to change
channel status while legacy management is still available. Sub2API mode disables
that legacy channel administration surface.

Stop and verify the old credential-refresh owner before transferring refresh
tokens to Sub2API. Access-only staging is distinct from refresh ownership. When
an account gains a refresh token, clear the temporary account-level access-token
expiry through Sub2API's account API; the credential's access-token expiry still
controls refreshing. Keep an expiry for access-only accounts.

Start New API with the persisted Sub2API configuration and the bridge with the
mandatory driver environment. Verify the maintenance API, account readiness,
HTTP/SSE/WS/Images, authorization isolation, and exact accounting before opening
admission. Preserve the session secret, database, namespace, identity secret,
queue/state, customer IDs and existing financial history.

Rollback is an application and routing rollback against the current customer
database. Do not restore a pre-cutover database over new completed charges. Stop
Sub2API refresh ownership and reconcile the newest refresh credentials before
reenabling the old refresher; a saved old refresh token can already be rotated.
Restore the backed-up bridge and service configuration, restore channel statuses
through the management API, and verify the closed-gate service before reopening.

This file describes the deployment procedure. It is not evidence that a
production cutover, public acceptance, or credential transfer has taken place.
