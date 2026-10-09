# Local Sub2API deployment record

**Status: OS_ELEVATION_CANCELLED — NOT DEPLOYED.** The user has authorized the
local production deployment, but the Windows elevation prompt was cancelled.
No new SCM services were created and the old production services remain running
with their existing configuration. Cutover and public acceptance did not occur.
The release owner updates this status only after an actual operation and its
matching acceptance receipt. Another machine remains subject to its own
[Windows deployment procedure](deploy/sub2api/NATIVE-WINDOWS.md) and acceptance.

## Planned topology and ownership

| Component | Host-local endpoint | Owner / purpose |
| --- | --- | --- |
| Existing public tunnel | `api.realyu.fun` to `127.0.0.1:18301` | Existing tunnel services remain the public ingress |
| Transparent edge | `127.0.0.1:18301` | HTTP/SSE/Responses WebSocket and native Images forwarding |
| Maintenance observation | `127.0.0.1:18302` | Admission marker and active request count |
| RealYu / New API | `127.0.0.1:18300` | Customer accounts, teams, authorization, subscriptions and accounting |
| Sub2API v0.2.15 standard | `127.0.0.1:28090` | Upstream OAuth accounts, scheduling and internal execution identities |
| PostgreSQL 18.6 | `127.0.0.1:28490` | Separate Sub2API database |
| Redis 8.10.2 community Windows build | `127.0.0.1:28391` | Sub2API cache, leases and coordination |
| Identity and internal credit worker | No listener | Persistent queue, bounded preparation, operator-visible failures |

The candidate rehearsal uses 28600/28601/28602 and a cloned RealYu database.
These endpoints do not prove the 18300/18301 production path has been switched.
Candidate edge validation has ended: the owned edge process was verified against
its retained PID/creation-time receipt and stopped at 2026-10-09 16:39:55 UTC;
listeners 28601 and 28602 are closed. No production process or listener was
stopped by this cleanup. The release owner also verified the candidate Go
executable hash and port ownership before stopping its 28600 process. The old
production chain remains running; the final public `/api/status` readback still
returned version `v3.9.2.22`. This was a read-only status check, not a production
inference acceptance.
The new supply uses its own PostgreSQL and Redis; customer financial data stays
in the existing RealYu database. Upstream user ownership is based on the actual
member and team, independent from the payer. Namespace and identity secret must
remain stable through restarts and migration.

Existing channels remain as historical rows. The new internal type 59 route
retains the existing `default` group and model IDs; ordinary API keys are not
reissued. Administrator requests explicitly pinned to an old channel ID need
their pin updated. Sub2API mode disables legacy channel management and refresh
tasks. Refresh-token ownership is transferred only after full acceptance and
verified shutdown of the previous refresher; access-only staging is separate.

## Release fields to fill after the actual operation

| Field | Current record |
| --- | --- |
| Source revision | Handoff branch `codex/sub2api-handoff-20261009`; run `git rev-parse HEAD` on the received checkout and verify `SOURCE-MANIFEST.json` |
| RealYu candidate version | `realyu-sub2api-v3.10.0-20261009` |
| RealYu candidate SHA-256 | `b008148e28522c052fb3e92172c4a8fc17170eae3726cb10c49c4cd45a091105` |
| Worker / edge source hashes | Final inputs are pinned by the reviewed private plan and source manifest; no installed receipt exists yet |
| SCM installation / automatic recovery | OS_ELEVATION_CANCELLED; no new services created |
| Admission drain and cold ledger backup | PENDING cutover receipt |
| Customer ledger preserved while closed | PENDING cutover receipt |
| Public acceptance operation ID / status | PENDING |
| OAuth refresh ownership transferred | PENDING separate official account API receipt |
| Reboot recovery | NOT_RUN unless an actual authorized host reboot is recorded |

## Evidence boundaries and first failures

- Edge transport: six real loopback socket tests passed, including live SSE,
  complete WebSocket frames/errors and maintenance behavior. This is transport
  evidence, not production or upstream availability evidence.
- Cutover lifecycle: simulated SCM with real temporary SQLite tests cover
  rollback without overwriting new charges, async settlement stability, state
  generation/subject checks, old verdict rejection and schema failure handling.
  These tests do not replace actual elevated SCM installation or public traffic.
- The 28601 candidate completed 13 functional categories across the original
  run and the separate image rerun: core HTTP/SSE, PDF, search, function tools,
  WebSocket, three scoped identities, and all eight text-model SSE requests.
  The original run passed 12 of 13 categories. Its Images request returned 403 because
  the official Sub2API group image flag was disabled. The first report remains
  preserved; a later image request succeeded after the official setting changed.
- The first immediate accounting snapshot showed a two-quota token discrepancy;
  later read-only database checks showed the asynchronous refund converged and
  matched successful usage. Keep both observations and use bounded settlement
  checks; do not silently replace the first failure.
- Native Images uses a different pricing path from the former bridge. The user
  explicitly selected the new `/v1/images/*` reported-token pricing, without
  adding the old Responses image tier fee. The observed 67-quota image transaction
  is consistent with that chosen policy. The exploratory CNY adaptation is not
  part of the release. Existing Responses image-tool pricing stays separate.
  Retain the original comparison evidence and this later policy decision;
  successful image bytes alone still do not establish correct accounting.
- Browser visual acceptance remains **BLOCKED** by repeated browser runtime
  timeout/reset. API results do not imply a completed visual acceptance.
- Public production acceptance, final native CLI dialogue and the additional
  real-provider cross-subject WebSocket check remain **NOT_RUN**. Candidate
  scoped responses are not by themselves proof of cross-subject isolation.
- Files API, legacy search/compact interfaces, requested image dimensions,
  desktop/CLI execution policy, the remote host and machine reboot retain the
  separate limitations recorded in the migration and E2E documents.

Sanitized reports are in [lab/sub2api_e2e](lab/sub2api_e2e/); request transcripts,
account identities, raw logs and receipts remain private. Every final PASS must
identify its tested binary/source and path. The historical reports are not
retroactively relabeled after a later build or fix.

## Executable handoff and rollback

### Resume the cancelled local operation

The deployment authorization remains recorded; Windows administrator elevation
still has to succeed through the normal operating-system prompt. Do not bypass
that prompt or interpret a cancelled prompt as a successful installation.

1. Recheck the old services, public ingress and maintenance marker. Confirm that
   no partially installed new SCM service or prior operation is active.
2. Rebuild the private plan from the final reviewed source and pin every input
   hash, including the parameterized service installer. Revalidate its roots,
   ports, current customer/queue coverage, worker readiness and schema identity.
   Do not reuse a plan whose source files have since changed.
3. Capture and validate the three temporary dependency process identities. Stop
   only those exact processes with the explicit host-tool stop option, so the
   new dependency services can bind their ports. Keep the old RealYu services
   running until the cutover helper has closed admission and drained traffic.
4. In a normally elevated PowerShell, run the reviewed `Install-HostRelease.ps1`
   with the private `-Plan` and matching `-ExpectedPlanSha256`. Its `-ValidateOnly`
   mode checks inputs without installing services. A validation PASS is not a
   release receipt.
5. Follow the private operation receipt. Supply a public acceptance verdict for
   that exact operation, binary and version within the bounded window. Only a
   final accepted receipt permits this document to claim deployment. Transfer
   refresh ownership separately after acceptance and old-owner shutdown.

The current public test fields remain NOT_RUN until that resumed operation
actually occurs. A failed or interrupted attempt retains its own receipt and
does not erase this cancelled attempt.

The transport package and existing-host procedure are described in
[EDGE-MIGRATION.md](deploy/sub2api/EDGE-MIGRATION.md). The reviewed host-specific
SCM tools are in [deploy/sub2api/host](deploy/sub2api/host/README.md).
`production_supply.py`, `production_route.py` and `host_cutover.py` consume
reviewed private plans; no plan, credentials or binary is included in Git.

`host_cutover.py` closes admission, waits for edge/backend traffic and a stable
ledger, changes routes through the official API, stops the old API/edge, takes a
cold backup, installs verified inputs, checks closed-gate ledger/schema state,
then provisionally opens for a bounded public acceptance window. The verdict is
bound to the operation, version and binary hash. Failure restores the old
program/routing against the **current** customer database; it never overwrites
new completed charges with a pre-cutover database snapshot.

An unexpected schema change blocks automatic old-program rollback and retains
the maintenance gate. If the API cannot be reached or a safe drain cannot be
established, the state is `recovery_required_drain`: the gate stays closed and
the operator reconciles process ownership and ledger stability before recovery.
That state is not a successful rollback. After refresh ownership transfers,
returning to the old refresher additionally requires reconciliation of the newest
rotated credentials; restoring an old secret file is insufficient.

The manually prepared initial job inventory on this host is an operational
artifact, not a separate product feature. A new machine uses the committed
automatic scanner and single worker to prepare permitted customer/team pools.
Preserve queue/state and unknown credit intents; do not reset them to force a
green status or repeat an uncertain top-up.
