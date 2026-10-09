# Sub2API integration and Windows Server handoff

The user subsequently authorized production deployment on the current host.
Its live version, public acceptance and retained first failures are recorded in
[LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md). This runbook covers the separate
future Windows Server migration; local publication does not establish acceptance
on that other machine. Never run two active production credential refreshers or
two independent writable copies of the customer ledger.
Target inventory received 2026-10-09: Windows Server 2025 Datacenter 24H2,
build 26100.32860, x64, 4 vCPU, 16 GiB RAM and about 158 GiB free on one system
disk. It reports no available Linux engine or nested-virtualization extensions.
The primary candidate path is therefore native Windows; see
[the native Windows guide](../../deploy/sub2api/NATIVE-WINDOWS.md).
The retained Compose files require a separately confirmed Linux environment.
No target service installation, database restore, reboot recovery or real
upstream E2E has been performed. Production Redis supply, encrypted off-host
backup destination, ingress and private administration remain to be decided.

## Ownership and topology

```text
Customer Codex / SDK / browser
           |
       TLS ingress (prepared separately; no current DNS changes)
           |
       RealYu / New API
       customers, teams, keys, subscriptions, customer billing and audit
           |
       channel type 59 / Sub2API integration
           |
       Sub2API standard mode
       per-identity upstream access, account groups, OAuth refresh, scheduling
           |
       authorized upstream supply
```

RealYu remains the only customer financial ledger. Sub2API standard mode has its
own balance/limit checks; it requires an internal supply-budget policy and
reconciliation, not an unsupported claim that all backend billing is disabled.
Do not map all customers to one shared Sub2API user: response ownership may be
checked at user scope even when API keys differ. The integration's persistent
identity mapping and secret must be included in private migration state.

Implemented configuration contract:

- `REALYU_UPSTREAM_DRIVER=sub2api` selects Sub2API ownership.
- `REALYU_SUB2API_ADMIN_URL` is a browser-reachable private management entry.
- `REALYU_SUB2API_BINDINGS_FILE` points to the read-only private JSON file;
  Compose maps `/run/realyu-integration/sub2api.json` from
  `${STATE_ROOT}/integration/sub2api.json`.
- JSON schema version 1 contains `namespace`, `identity_secret` (at least 32
  random characters), `admin_api_key`, `pools`, `provision` and `bindings`.
  Start from `deploy/sub2api/bindings.example.json`; zero IDs and blank secrets
  are deliberately unusable. Each pool's internal `base_url` must equal its
  type-59 channel URL, with no `/v1` suffix; `group_id` selects the Sub2API group.
- Runtime projection derives actual member plus workspace identities and keys from the
  persistent secret/namespace; it caches in memory and rebuilds via Sub2API after
  restart, without writing the bindings file. Secret rotation is a separate
  migration, not a restart operation.
- Production configuration defaults to `provision.mode="prewarmed"`; customer
  requests restore existing projected identities through read-only queries and
  must not create/login users on their first inference request. Run the separate
  `cmd/sub2api-prewarm --watch` worker before admission. `isolated-lazy` is
  restricted to loopback test pools.
- The gateway reconciles committed enabled users, workspace members and allowed
  routes every 30 seconds into `REALYU_SUB2API_QUEUE_DIR/<config SHA>/`.
  This uses read-only customer DB queries and never joins signup/funding writes.
  The separate worker reads immutable jobs and writes progress/heartbeat to
  `REALYU_SUB2API_STATE_DIR`; neither projection nor enrollment updates wallets.
  Customer readiness is exposed by `/api/workspace/sub2api/status`; it describes
  identity preparation, not upstream account health or a successful model call.
  Required candidate integration/target acceptance is recorded separately.
- `provision.initial_balance=100` is an example supply-side budget, not money
  copied from customer wallets. Without explicitly enabled credit management,
  exhaustion is not automatically replenished.
  Choose and monitor the actual budget/concurrency explicitly.

**Production gate: internal credit headroom.** The actual isolated standard
group was read with multiplier 1 and the projection seed is 100. Stock v0.2.15
deducts `actual_cost` from that user balance and rejects exhausted/below-reserve
balances, independently of RealYu customer entitlement. A ready identity does
not prove funding headroom. Stock official group/user-group rates must be >0;
setting the upstream account's statistics multiplier to zero does not remove
the projected user's balance gate. Do not bypass it with direct DB edits.

Use explicitly managed internal operating credit, with a safety reserve and
low-water monitoring sized for peak usage and the intervention window. Refill
through official `POST /api/v1/admin/users/:id/balance`, `operation=add`, a
durable operation ID/`Idempotency-Key`, purpose notes and readback/audit. Avoid
`set` and blind retry after uncertain results or an expired deduplication window.
Keep stock admin-recharge affiliate rewards disabled for this dedicated backend
and verify projected identities have no invite relationship. None of this is a
customer top-up or a mirror of RealYu balances. Keep raw `total_cost` and billed
`actual_cost` for supply audit. Increasing the seed changes only new users;
existing identities are deliberately not reset or refilled by projection.

The watcher now implements explicitly enabled `credit` low-water monitoring
and fixed-amount replenishment for this installation's DONE projected identities.
Its private `credit.json` records an immutable intent before each official add;
unknown results/crash interruption stay blocked by default, and an operator may
replay only the same key/config/amount inside the finite verified window. Read
429s and transient failures also persist backoff; permanent read denials block.
The heartbeat exposes credit failures even when all identity tasks are DONE.
Scanning is paged at 50 identity inspections; 10,000 local audit operations
require verified private archival with no unresolved intents. The native guide
contains configuration, alert/restore and safe archival procedures. Production
sizing and target acceptance remain required; no customer financial state or
production credit was changed. The separate stock-process credit acceptance
passed 10 checks: one user across two pools received one additive credit/audit,
1 + 100 = 101, restart did not repeat it, and no RealYu database or model request
was used. See `lab/sub2api_e2e/credit-watch-summary.json` for the exact worker SHA.

Sub2API owns the migrated account credentials and refresh lifecycle. The new
candidate does not start the old `image_bridge.py`, `watch-auth`, or custom OAuth
refresh manager. RealYu forwards HTTP and WebSocket directly through its Go
gateway. Keep old source/history for rollback/audit, but do not run both owners
for the same accounts. A website menu replacement alone is not completion of this
ownership migration.

## Reproducible code baseline

The old host's active source is `C:/srv/realyu-team-funding-lab`, not the historical
pilot at `C:/srv/realyu-newapi-lab`. The latest pre-integration production baseline
is `realyu-provider-v3.9.2.22-20261008`, binary SHA-256
`f729e2b8f2c631e1b804b7e6ef56397efbe76560c2e8fc6528f845c2eff4de20`.
Its frozen source is `.lab/usage-admin-redesign-20261008/source`. All 2,631 entries
of its recorded source manifest were rehashed for this migration audit: zero
missing files and zero mismatches. This verifies the local freeze, not a new
production deployment.

The migration branch starts from the existing repository and overlays that
frozen source, then adds reviewed integration changes. The freeze does not
contain root license/build files or maintenance scripts, so copying only that
folder is insufficient. The shared development tree has additional uncommitted
features and is not an interchangeable release input. Preserve the frozen
`web/public` download assets; copying the smaller shared download folder loses
published installers. The precise final commit, upstream tag/digest and test
receipts belong in the final acceptance report; never substitute an old Git HEAD
or an old green receipt for those values.

Code to Git:

- Reviewed Go host + independent `relaykit`, frontend source and lockfiles,
  synthetic tests, public installer source/assets or a hash-verified retrieval
  manifest, safe deployment templates and this handoff.
- `LICENSE`, `NOTICE`, `THIRD-PARTY-LICENSES.md` and existing New API attribution.
- Sanitized acceptance receipts that contain synthetic identities and aggregate
  evidence; no real prompts, account identifiers, cookies or bearer tokens.

Keep out of Git and Docker contexts:

- All `.lab` content, production/new test databases and WAL/SHM files, customer
  ledger exports, private env/config, OAuth/API credentials, session/crypto/TOTP
  secrets, Cloudflare tunnel credentials, payment merchant keys and certificates.
- `lab/results`, `lab/handoff`, screenshots/ZIPs/raw logs until explicitly
  reviewed and sanitized; generated reports may include customer information.
- Runtime data, backups, temporary cloned databases, `node_modules`, build
  caches, desktop Codex homes and machine-specific service installation state.

The repository's existing `origin` is the **public** RealYu fork
`zhang709571153/new-api` (GitHub visibility checked 2026-10-09); `upstream` is
QuantumNous/new-api. The old development HEAD has 19 local RealYu commits not
reachable from the inspected remote refs, including historical probe reports and
screenshots. The handoff therefore uses a clean orphan snapshot with an explicit
source/test/build/assets allowlist, preserving all licenses and attribution,
instead of publishing those local ancestors. Removing files in a new commit does
not remove their contents from its parents, and a new orphan branch does not
clean any previously published branch. No history rewrite or secret rotation is
part of this task.

The intended handoff branch is `codex/sub2api-handoff-20261009`; this is a
planned destination until the lead executor records a successful push and the
exact remote commit. `deploy/sub2api/export_source.py` copies selected source,
tests, build inputs and the complete 38 public downloads into a new directory,
writes a byte/hash manifest and includes no `.git` ancestry. The lead executor
must inspect that concrete export, scan its contents, initialize a clean orphan
snapshot and push only the reviewed branch. The exporter itself never commits,
pushes, publishes or establishes that content is secret-free. Keep the intended
branch, actual pushed SHA and verification SHA distinct until they are recorded.

The current repository workflows publish on tag push or explicit manual dispatch;
ordinary branch pushes do not match those release triggers. Do not push tags or
dispatch release workflows. External webhooks were not audited. Push only the
intended candidate branch to the authorized fork; never push private state or
publish/merge a release as part of a handoff.
Both the staged diff and newly added binary archives must be inspected before
pushing. A Git clone alone does not restore customer data or secrets.

The old `lab/onboard_codex_pool.py` contains non-example account emails and is
excluded from the handoff. Historical `lab/results` includes identity fields and
PNG screenshots; textual pattern scans do not prove images or all Git history
are free of customer information. The bounded audit found no confirmed usable
credential, which is not a guarantee of exhaustive secret detection.

Public download completeness is a separate build prerequisite. The inherited
Git ignore rules omit `realyu-client.exe`, four pinned CPython runtime archives and
the public CA bundle. Four SDK ZIPs are approximately 16–20 MB each; the largest
legacy runtime is approximately 35 MB. No inspected individual public asset
exceeds 50 MiB, but those files collectively add substantial Git weight. Either
include the reviewed public artifacts explicitly in the handoff or provide an
independent hash-verified artifact source that remains available after retiring
the old host. Merely pointing a future rebuild at the old site is insufficient.
Verify all 38 baseline public download assets before frontend/Go builds. Keep
`lab/realyu-pricing-20260924.json`: the pricing regression tests read it directly.
`web/dist`, `node_modules` and local caches must be recreated, never assumed to
arrive in a clean clone. The legacy `prepare-unix-client.py` also rewrites its
bootstrap source; do not run it indiscriminately over accepted frozen installers.

## Host inventory and isolated installation

1. Preserve the destination's existing cloud agents, monitoring, remote access,
   Codex services and tasks. Do not copy its full report, IPs or account names
   into public Git. Repeat the read-only `Inspect-WindowsHost.ps1` when needed.
2. Follow `NATIVE-WINDOWS.md`: PostgreSQL 18 native distribution and pinned native
   Sub2API plus RealYu binaries. Free Redis supply is preferred: the community
   Windows build is a candidate subject to persistence/recovery/load acceptance,
   with its upstream Linux-production recommendation preserved. External Redis
   is another option; commercial Memurai is not mandatory. Memurai Developer's
   production prohibition and ten-day stop apply specifically to that product.
3. Clone the exact reviewed candidate commit, not the old default branch.
   Verify licenses, lockfiles, all public assets and binary hashes. The target's
   Codex-cache Node/Python/Git are not permanent background service dependencies.
4. Keep filled private configuration outside Git. Run `New-NativeCandidate.ps1`
   with `-ValidateOnly` first, then generate a new `.../candidates/<name>` directory.
   It writes restrictive ACLs and manual-start WinSW definitions without
   installing or starting services. SQLite is fixed to a fresh candidate path.
5. Use synthetic customer state and dedicated test supply. The generated
   `TOKEN_REFRESH_ENABLED=false` disables background refresh only; request-time
   refresh can still occur. Never import production refresh tokens during this
   rehearsal. Do not copy the old host's local proxy address blindly.
6. Perform explicit target-only service and dependency acceptance, then bootstrap
   type 59 through the existing authenticated API and switch the stopped
   candidate to its pending Sub2API XML as documented. Keep HTTP ports `23000`
   and `28080` on loopback and the Sub2API management surface private.
7. Verify separate customers, team members and keys, billing owner preservation,
   HTTP and WS continuation, actual native Codex behavior, restore and reboot
   recovery. Fixture/packaging tests do not establish target acceptance.

If a Linux host is separately selected, the Compose alternative can be validated
with `config --quiet`; PostgreSQL and Redis have no published host ports and both
application ports are loopback-only. Do not assume a Windows-container engine
can run those Linux images. Container data belongs on the Linux filesystem.

Sub2API v0.2.15's first administrative operation can return HTTP 423 from
`AdminComplianceGuard` until the operator completes its electronic confirmation.
The operating user explicitly authorized the lead executor to complete
the current isolated instance's normal confirmation flow after reviewing
`docs/legal/admin-compliance.zh.md`. That isolated confirmation returned HTTP 200,
the guard reported no confirmation outstanding, and the administrative groups
read then returned HTTP 200; the private receipt is not included in Git. This is not blanket
consent for future instances or a reason to bypass the guard. Bootstrap scripts must
not silently call an acknowledgment API, write acceptance into the database, or
disable/bypass the guard. Until the operator confirms, report affected real
upstream/provisioning cases as `BLOCKED` while continuing independent static and
fixture tests. Do not describe a 423 response as an account-pool compatibility bug.

The frontend is built with Bun and `web/bun.lock`; Go uses the root and independent
`relaykit/go.mod`. `Dockerfile.realyu` builds both frontend and Go and embeds static
assets. Build success is separate from a restored database migration and from
native Codex acceptance. The native Windows packaging path is independently
documented and has no dependency on a Linux engine.

## Private state inventory and backup

The current RealYu website's customer/financial state is the running release's
`.lab/new-api.db`; identify it from the running service/config, not cwd. Preserve
the existing session and crypto secrets, payment/OAuth settings and any separate
log DB. No ledger reset, reinitialization, duplicate welcome credit, or replay of
historical funding migrations is part of replacing the upstream driver.

For a rehearsal copy, use Python 3.11+:

```powershell
python deploy/sub2api/snapshot_sqlite.py --source '<verified running release>/.lab/new-api.db' --destination '<private backup directory>/realyu-rehearsal.db'
```

The script reads the source in read-only mode and uses SQLite's online backup
API, so committed WAL data is included. It refuses overwrite and runs
`integrity_check`, recording SHA-256 and per-table counts in a private manifest.
It never copies the `.db` alone while discarding a live WAL. A live snapshot
does not include subsequent writes and cannot be the final handover snapshot.

Once Sub2API contains accounts/state, back up all of:

- PostgreSQL logical dump plus its role/database ownership and exact major
  version. Inside the guest use `docker compose ... exec -T postgres pg_dump
  -U sub2api -d sub2api -Fc > /private/backups/sub2api.dump`. This redirection is a
  Linux shell command; do not pipe binary dumps through old Windows PowerShell.
  Native Windows instead uses `pg_dump.exe --format=custom --file=<private path>`
  and a private ACL-protected `PGPASSFILE`; never put database passwords in CLI
  arguments or a published script. See the native guide for restore details.
- `/app/data` and the private env/config, including fixed JWT/TOTP keys, the
  integration identity namespace/secret and provisioned service credential.
- Enrollment jobs, per-generation worker progress, heartbeat and the shared
  configuration-adjacent cooldown/lock metadata. Preserve durable cooldowns and
  failed-task evidence; a completed job is not a substitute for Sub2API DB restore.
- Redis AOF/RDB or an explicit documented decision to discard only transient
  leases/cache during a fully drained restart. Do not restore stale in-flight
  leases into a live mixed cluster and assume concurrency is correct.
- RealYu's consistent SQLite snapshot, retained private artifacts and all
  financial/audit state. Store an encrypted off-host copy; keep the decryption
  material separate and accessible to the authorized operator.

For cross-component consistency, the final backup requires an authorized traffic
drain, zero in-flight requests and stopped writers/refreshers. Do not execute this
on production during development. Do not use `docker compose down -v` or
overwrite old volumes as an upgrade/restore procedure.

## Restore rehearsal and acceptance

Restore into **new, empty private directories**. Keep old backups untouched.
Verify hashes before restoring. Initialize PostgreSQL with the compatible major
version and restore using `pg_restore --exit-on-error --no-owner -U sub2api
-d sub2api` against the new empty candidate database. A populated target requires
an explicit separate restore plan; never add `--clean` to a production command.
Restore application data/config and RealYu SQLite while their application
containers are stopped. Preserve secret values, permissions and timezone.

Before a cloned production database can execute background jobs, restrict its
egress and audit refresh/payment/scheduled-task settings on the copy. Existing
upstream channels may start refresh work before a browser test. Rehearsal must
not create a second live owner of production account tokens or payment callbacks.
Verify restored table counts, financial totals, subscription expiry/windows,
token ownership, team boundaries, pricing, original login and original API keys.
Then enable only the dedicated synthetic test channels for functional smoke.

Strict acceptance includes:

For stock v0.2.15 OpenAI OAuth supply, official account editing must enable
`extra.openai_oauth_responses_websockets_v2_enabled=true` and select `ctx_pool`
WebSocket mode. The initial real WS test returned 1013 / no available account
with the capability disabled; official configuration then allowed real two-turn
same-socket continuation. Current native compaction v2 replay passed, while the
legacy `/responses/compact` returned upstream 404. Keep those capabilities and
exact tested binary hashes separate; do not label all compaction unsupported
or infer WS availability from ordinary HTTP success.

- Full website routes, login/logout, roles, team ownership and original customer
  key compatibility; production-style data restoration and asset hashes.
- Responses/Chat streaming and nonstream terminal outputs, PDF URL/base64,
  image reference retention, real web-search events/citations, tools, usage and
  image charges; unsupported operations must return explicit errors.
- Native Codex new/continued sessions, HTTP and supported WS paths, cancellation,
  long context/compaction, concurrent requests and reconnect after restart.
- Two unrelated users and multiple keys: response IDs/session identifiers must
  not reveal another user's response or acquire another user's dedicated pool.
- Account exhaustion/fallback, backend restarts and slow/aborted streams with
  no duplicate customer settlement. Match request IDs and actual usage to the
  customer ledger and supply-side audit record.
- A bounded repeated-load run with fixed concurrency, duration, first failure,
  success criteria and error distribution. Report samples and remaining gaps;
  a health endpoint or several successes is not long-term stability proof.
- A backup/restore drill and host reboot/startup drill on the target machine.

Independent native persistence fixtures passed 12 checks for PostgreSQL 18.6
and community Redis 8.10.2 MSYS2: committed-row restart and fresh-database dump
restore, Redis authentication/Lua/TTL, AOF normal/crash recovery and rewrite,
typed values and standalone RDB recovery. All fixture-owned processes stopped;
see `deploy/sub2api/native-persistence-validation.json`. This is not target SCM,
host reboot, disk-full, production-data recovery or long-duration load acceptance.

The prewarm worker keeps one OS lock per canonical bindings path and a durable
shared cooldown. Separate hosts/config copies do not have a global quota lock;
use one scheduler per Sub2API deployment and coordinate other callers sharing
the upstream IP. Queue generations follow the exact bindings SHA, and a changed
generation is refused before provisioning. BLOCKED/FAILED jobs stop the queue
until an operator resolves the cause and explicitly resumes it; restarts do not
retry those side effects. Jobs are retained. Revoking a member stops RealYu
access but does not delete an already queued internal job; cancellation/reclaim
of obsolete internal users is a remaining operational workflow. A failed stale
job can halt later enrollments, so readiness/heartbeat alerts need an operator.

Record `PASS`, `FAIL`, `BLOCKED` or `NOT_RUN` per case; retain first failures. Mock
upstream E2E and real upstream E2E must be labelled separately. Public DNS and the
native desktop on the target host remain unverified until tested there.

## Future cutover and retirement (production remains on hold)

Only after an explicit production instruction and acceptance, record a maintenance window and rollback
owner. Prepare target TLS, payment/OAuth callback addresses, large request limits,
SSE no-buffering, WebSocket upgrades and long-stream timeouts. Preserve the
public hostname if customers should retain their current configuration.

Drain old ingress, wait for all accepted requests and settlements to finish,
stop old writers/refreshers, take and verify the final consistent backup, restore
the target, and establish a **single** writer/refresh owner. Change the domain or
tunnel route only then. Run public real-client and ledger acceptance before
accepting migration as complete. Switching Cloudflare connectors to two hosts
while each holds an independent SQLite ledger would create split-brain; never
use it as a zero-downtime shortcut.

Rollback before target writes can restore the old route and service. After
target financial writes, do not simply start the stale old database: reconcile
or transfer the latest state under a new drain first. Keep the old host and
encrypted final backups available until target acceptance and an agreed
observation window pass. Only under the subsequent production instruction should
the migration retire old service/tunnel state using its recorded inventory. Preserve the
recovery archive; do not treat target installation alone as permission to stop
the old host early. Record actual cutover and retirement actions separately from
candidate development evidence.

For a permanently revoked subject whose retained job blocks the queue: stop only
the candidate worker, verify RealYu access is revoked, archive that exact job
outside the scanned queue tree, retain progress for audit, then restart. Do not
archive an eligible subject to fabricate readiness; fix its supply configuration
and explicitly resume instead. Graceful interruption during projection retains
RUNNING/cooldown for idempotent restart recovery; permanent errors stay blocked.
