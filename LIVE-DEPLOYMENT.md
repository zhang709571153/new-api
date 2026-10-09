# Local Sub2API deployment record

**2026-10-10 status: first cutover rolled back; corrected candidate accepted;
second public cutover pending.** Production currently runs
`realyu-provider-v3.9.2.22-20261008`. The four new dependency services are already
installed and running automatically under LocalService. Deployment authorization
remains in force. The normal elevated Windows entry must execute the new pinned
plan; source delivery and candidate acceptance are not public deployment.

## Topology

| Component | Host-local endpoint | Responsibility |
| --- | --- | --- |
| Existing public tunnel | `api.realyu.fun` to `127.0.0.1:18301` | Existing public ingress |
| Transparent edge | `127.0.0.1:18301` | HTTP/SSE, Responses WS and native Images |
| Maintenance observation | `127.0.0.1:18302` | Admission and active requests |
| RealYu / New API | `127.0.0.1:18300` | Users, teams, permissions, plans and customer ledger |
| Sub2API 0.2.15 standard | `127.0.0.1:28090` | Upstream accounts, scheduling and projected identities |
| PostgreSQL 18.6 | `127.0.0.1:28490` | Separate Sub2API state |
| Redis 8.10.2 community Windows build | `127.0.0.1:28391` | Cache, leases and coordination; no paid Redis dependency |
| Identity and internal-credit worker | No listener | Persisted preparation and credit intents |

The isolated candidate uses 28600/28601/28602 and its own RealYu database copy
and namespace. Neither its balances nor its response ownership are production
acceptance. Preserve the production namespace and identity secret on migration.
The member/team identity is distinct from the customer who pays the charge.

## Actual first attempt

The cancelled elevation attempt was followed by a successful manual elevated
installation at 2026-10-09 16:51 UTC. All four new services were installed. The
first gateway cutover opened for public testing at 16:52 UTC. It took an integrity
checked cold backup, preserved the customer ledger and changed no database schema.

Public PDF inline/link reading, web search with citations, HTTP/SSE and chat
passed. Account 1 returned upstream WS 1011 and was quarantined through the
normal Sub2API admin API. A separate run using the remaining supply passed two
WS turns, three identities, validation rejection and native image generation.

The real cross-subject WS check then selected a retained upstream connection
and timed out with a busy error. No foreign content or charge was observed, but
there was no authoritative ownership denial. This was treated as P0. The gateway
program and routing were rolled back against the current customer database;
the backup database was never restored over new transactions. Refresh tokens
have not moved, and the original refresh owner remains responsible while legacy
production is active. All first-failure evidence remains private and unchanged.

## Corrected candidate

Version `realyu-sub2api-v3.10.0.1-20261010`, binary SHA-256
`91a9dc35306ae1fe29f3326d91af92ec855e25df09f6e56c9678d359a7864b8a`.
The worker remains at SHA-256
`8ac607bf72faee338238b5393555d326d910662b76503d4cc4854d0dda75710c`.

Authenticated opaque response IDs now bind retained continuation to the member
and team. Foreign, unsigned and tampered references are rejected locally before
upstream lookup. See [ownership behavior](lab/sub2api_e2e/RESPONSE-OWNERSHIP.md).
The exact candidate passed 13 functional categories, all eight text models,
eight real HTTP/WS ownership checks, exact denied-request no-charge checks and
Codex 0.162.0 initial dialogue plus separate-process resume. See the
[sanitized candidate report](lab/sub2api_e2e/release-scope-candidate-20261010.json).
These results still require a separate production run after the second cutover.

Native `/v1/images/*` uses the new interface's reported-token pricing by the
user's explicit decision. The former Responses image-tool tariff is unchanged.
Successful image bytes alone are insufficient; reconcile usage, token quota,
wallet/subscription payer and team allowance after settlement.

## Retry and migration

Use a fresh operation directory and pinned inputs, including the exact gateway,
edge, management helper and service verifier. A plan with `dependency_mode:
"reuse"` verifies the existing installed dependency services under normal Windows
administrator elevation; it does not reinstall them. `production_route.py
restage` validates the pinned previous restored receipt and unchanged channel
baseline without modifying routing. Never reuse a completed operation directory.

The reviewed `Install-HostRelease.ps1 -Plan ... -ExpectedPlanSha256 ...` entry
checks every pinned input. `-ValidateOnly` performs no service mutation.
`host_cutover.py` closes admission, drains active work and async settlement,
activates the route through the official API, stops the old gateway/edge,
takes a cold backup, installs verified inputs and compares closed-gate ledger
and schema. It provisionally opens for a 20-minute public acceptance window.
Only a matching operation/version/binary PASS verdict publishes the release.
Failure restores the old program/routing while preserving the latest ledger.
Unexpected schema changes or an unverified drain retain maintenance for recovery.

After public acceptance, verify the old refresh process is gone and old channels
are disabled before transferring renewable credentials through Sub2API's normal
account API. Account 1 remains inactive; it has no refresh token. Accounts 2 and
3 have renewable credentials. After transfer, rollback also requires the newest
rotated credentials; an old secret backup is not a safe refresh-owner rollback.

Another Windows Server uses [NATIVE-WINDOWS.md](deploy/sub2api/NATIVE-WINDOWS.md)
and [the migration runbook](lab/maintenance/sub2api-migration.md). Copy current
private databases, bindings, queue/credit state, credentials and tunnel material
through a protected channel, separately from Git. Do not allow two machines to
write independent customer ledgers or refresh the same OAuth credentials.

## Remaining boundaries

- Files upload/file IDs and old search/compact interfaces retain compatibility
  limits. Inline and URL PDFs and current `web_search` are separately tested.
- Images may return 1254 by 1254 when 1024 by 1024 was requested.
- Old unsigned retained response IDs and unauthenticated retained item/conversation
  references are rejected; full inline history remains supported.
- Browser UI acceptance was blocked by browser runtime failures. CLI dialogue
  acceptance does not imply file/tool execution or desktop UI acceptance.
- Automatic startup is configured; no actual whole-machine reboot was performed.
  Finite smoke observations are not a long-term stability or availability SLA.
- Non-P0 compatibility issues are tracked for subsequent fixes, per user direction.
  A real isolation or accounting P0 still requires stopping the affected release.

Raw transcripts, customer identities, secrets and private receipts are excluded
from Git. Historical reports are preserved with their original binary hashes.
