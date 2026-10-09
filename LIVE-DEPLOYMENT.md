# Local Sub2API deployment record

**2026-10-10 status: PUBLISHED AND PUBLICLY ACCEPTED.** Production runs
`realyu-sub2api-v3.10.0.1-20261010`, verified through `api.realyu.fun`. The second
cutover completed with customer ledger preserved and no schema change. The four
new dependency services run automatically under LocalService. This records an
actual local-host publication; another machine remains separately undeployed.

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

The isolated candidate used 28600/28601/28602 and its own RealYu database copy
and namespace. Neither its balances nor its response ownership are production
acceptance. Preserve the production namespace and identity secret on migration.
The member/team identity is distinct from the customer who pays the charge.
The owned candidate gateway and edge were stopped after public acceptance.

## Accepted production operation

Operation `sub2api-scope-20261009T172526Z-3ee7e037` reached `published` after
matching public acceptance. Source build revision was `05a732e7e24acc129e9bfc8808d9ffe718608b83`;
later handoff changes only improve deployment helpers and evidence. Dependency
verification passed 24 checks, including exact running executable paths and
pinned application hashes under normal administrator elevation.

- 13 public SDK categories passed, with 23 bounded request attempts, including
  all eight text models, PDFs, real search/citations, tool round trip, WS and Images.
- Eight HTTP/WS ownership checks passed, including cross-person, personal/team,
  unsigned/tampered ID rejection, original-owner continuation and zero denied charges.
- Codex CLI 0.162.0 completed a fresh dialogue and a separate-process resume.
- Twenty financial invariants matched across 47 dedicated-operator usage records
  after the refresh follow-up: no duplicate settlement, exact wallet/token usage,
  team subscription/member allowance attribution and native image token pricing.
- The requested bichon/hilichurl scene was generated through the public Images
  API and visually reviewed. Input 172 + output 601 tokens settled to 97 quota
  at model ratio 1 and group ratio 0.125, without an extra per-image tool charge.
- Both renewable accounts transferred and successfully refreshed through the
  official Sub2API API. SSE, WS continuation and three identities passed afterward.
- Two dedicated operator users and personal keys were disabled. All four keys,
  including workspace keys revoked by disabled-user status, were denied. Financial
  records were preserved. The fixture is no longer available for inference.

See [the sanitized public report](lab/sub2api_e2e/release-public-20261010.json).
Cold backup SHA-256 was
`e3fabff39d55cb8cf161a7dfc84db11b5c5cb106d66cf76ee5f149ebd78376cf`;
it is retained privately and was not restored over newer customer transactions.

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
had not moved at that rollback. All first-failure evidence remains private and
unchanged; the later accepted operation transferred refresh ownership separately.

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
The separate production run is recorded above; historical candidate reports keep
their original candidate-only scope.

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

After public acceptance, the old executable was confirmed absent, the legacy
auth-sync flag was false, the Sub2API driver was active and channels 1/2/3 were
disabled. Credentials for accounts 2/3 then transferred and actually refreshed.
Account 1 remains inactive and has no refresh token. The first readback incorrectly
expected secrets in the redacted ordinary GET. A single-account official export
verified the existing write without replay; the helper now uses that verification
and has regression tests. No export credentials are printed or written to Git.
Rollback now requires the newest rotated credentials from Sub2API; an old legacy
secret backup is no longer a safe refresh-owner rollback.

Another Windows Server uses [NATIVE-WINDOWS.md](deploy/sub2api/NATIVE-WINDOWS.md)
and [the migration runbook](lab/maintenance/sub2api-migration.md). Copy current
private databases, bindings, queue/credit state, credentials and tunnel material
through a protected channel, separately from Git. Do not allow two machines to
write independent customer ledgers or refresh the same OAuth credentials.

## Remaining boundaries

- Files upload/file IDs and old search/compact interfaces retain compatibility
  limits. Inline and URL PDFs and current `web_search` are separately tested.
- Images may return 1254 by 1254 or 1536 by 1024 when 1024 by 1024 was requested.
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
