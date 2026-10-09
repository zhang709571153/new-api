# RealYu / Sub2API candidate deployment

This directory contains deployment and migration tooling. The user subsequently
authorized production deployment on the current host; its exact live version,
acceptance results and first failures are recorded in
[LIVE-DEPLOYMENT.md](../../LIVE-DEPLOYMENT.md). Another Windows Server still
requires its own environment preparation, private state transfer and acceptance.
Never run two active production credential refreshers or independent writable
copies of the customer ledger. Rehearsal commands use isolated state.

Read [the complete handoff](../../lab/maintenance/sub2api-migration.md) first.

The destination inventory confirms Windows Server 2025 without an available
Linux engine or reported nested-virtualization extensions. Follow the
[native Windows candidate guide](NATIVE-WINDOWS.md) as the primary path.
The Linux Compose path below is retained as an alternative for a separately
confirmed Linux host; it is not a ready-to-run choice on this destination.

Files:

- `compose.yaml`: Linux-engine Sub2API standard mode + PostgreSQL + Redis; optional
  `portal` profile for the RealYu candidate. Both HTTP ports bind to loopback.
- `Dockerfile.realyu`: builds the reviewed source and frontend, preserving the
  upstream New API notices. The root `.dockerignore` excludes private state.
- `.env.example`: empty secret placeholders. Copy outside Git and fill privately.
- `bindings.example.json`: nonworking schema for the private identity mapping;
  supply actual channel/group IDs and credentials in the private copy only.
- `Inspect-WindowsHost.ps1`: read-only Windows Server inventory; does not install
  Docker/WSL/Hyper-V or start background runtimes.
- `New-NativeCandidate.ps1` and `native-windows.example.json`: hash-verified,
  ACL-protected native candidate packaging only; no service installation/start.
- `test_native_candidate.py`: Windows packaging safety tests using a separately
  downloaded, pinned WinSW artifact; does not exercise SCM or model requests.
- `snapshot_sqlite.py`: Python 3.11+ consistent SQLite backup using the backup API;
  never performs a cutover or overwrites an existing destination.
- `test_snapshot_sqlite.py`: isolated WAL, overwrite and source-protection tests.
- `bootstrap_route.py` and `bootstrap-route.example.json`: create the internal
  type-59 route through the existing authenticated API before enabling the
  Sub2API driver; never changes the database directly or unlocks channel APIs.
- `prewarm-jobs.example.json`: nonworking template for the private, reviewed
  actual-member/workspace/channel queue consumed by `cmd/sub2api-prewarm`.

### Persistent enrollment before customer readiness

The private bindings JSON defaults to `provision.mode="prewarmed"`. Keep this
mode for production. Normal requests only recover existing identities through
read-only management queries; they cannot create users/keys or consume upstream
login capacity. Unprepared subjects fail closed. `isolated-lazy` is restricted
to loopback fixtures.

The seed balance 100 and credit thresholds are examples requiring capacity sizing. Standard
Sub2API still deducts internal user credit and rejects exhaustion even if the
customer has paid RealYu. Stock group rates cannot officially be zero; account
statistics rate zero does not remove this gate. Read the native guide's required
internal-credit policy: safe headroom, low-water monitoring, official audited
idempotent `add` adjustments and recovery acceptance, without customer-wallet
sync or a second customer charge. The same watcher now supports explicitly enabled `credit` low-water monitoring
and audited fixed-amount replenishment. Its private durable intents, bounded
retries, finite replay window and credit heartbeat warnings are described in
the native guide. Unknown outcomes block until operator reconciliation; no
customer wallet is read or changed. Production sizing/alert ownership and target
acceptance remain required, and readiness never guarantees future headroom.

RealYu reconciles committed enabled users, valid team members, tokens and
permitted routes every 30 seconds using read-only DB queries. Immutable jobs are
published to `REALYU_SUB2API_QUEUE_DIR/<config SHA>/<user>-<team>-<channel>.json`.
`realyu_user_id` is the actual member, not the payer; team zero is personal.
The authenticated workspace status endpoint can also enqueue the current scope.
Neither producer adds upstream calls or row locks to signup/funding transactions.

Build the independent worker from the same commit:

```powershell
go build -trimpath -o '<private artifact directory>/sub2api-prewarm.exe' ./cmd/sub2api-prewarm
```

The native packager generates a manual-start `prewarm-service.xml` running
`--watch`. It sets the bindings path plus queue/state directory environment
variables. The worker scans every 5 seconds and writes a heartbeat every 15
seconds during long batches. In Linux Compose the `portal` profile includes
both gateway and worker; create private `integration`, `enrollment-queue` and
`prewarm` directories under `STATE_ROOT` before startup. The worker mounts jobs
read-only and progress read/write; the gateway mounts the opposite. Both bind
configuration content read-only, while the worker may write adjacent lock/rate
metadata. The native candidate shares LocalService, so equivalent per-process
ACL separation still requires target service-SID setup and validation.

`STATE_DIR/<config SHA>.json` contains progress, `.queue.json` the batch snapshot,
and `heartbeat.json` liveness. All are private identity metadata. Configuration
generation changes are checked before side effects and do not inherit old ready
state. `/api/workspace/sub2api/status?scope=personal|team|current` reports only the
current authenticated scope. `ready` proves identity preparation, not upstream
supply health or successful inference.

The OS lock and durable cooldown are shared by all batches using one canonical
bindings file; another state path cannot bypass them. Use one scheduler for each
Sub deployment. Different hosts/config copies and other callers sharing the same
upstream IP still require coordination. 429 waits at least 60 seconds; permanent
BLOCKED/FAILED outcomes require operator action and are not retried by restart.
Stop only the candidate worker, resolve the cause, and explicitly run the current
snapshot/state with `--queue <snapshot> --state <state> --resume-blocked`, then
restart the watcher. Never delete cooldown/progress to avoid the wait or add
resume by default to a long-running service.

Automatic scanning/enqueue is connected in the candidate; target acceptance is
still separate. Retained jobs are not automatically canceled when a member loses
access. RealYu authentication blocks revoked users, but reclaiming obsolete
internal identities and unblocking a stale failed job remain operator workflows.
One failed job stops the batch, so monitoring must act on pending/error/heartbeat
states. The worker never opens the RealYu DB or changes customer wallets.
A 423 is not permission to bypass operator confirmation.

### Creating the internal route

For stock Sub2API v0.2.15 OpenAI OAuth accounts, enable
`extra.openai_oauth_responses_websockets_v2_enabled=true` through the official
account editor and select `ctx_pool` for its WebSocket connection mode. The
initial real WS run returned close code 1013 / no available account before that
setting; after configuration, same-socket two-turn continuation passed. HTTP
success alone does not establish WS account scheduling. See the native guide.
Native compaction v2 replay also passed; the older `/responses/compact` returned
upstream 404 and remains a separate legacy-interface limitation. Exact tested
binary hashes and scope are in `lab/sub2api_e2e/real-chain-features-summary.json`;
those results are not target-host or desktop-client acceptance.

Run only on an isolated candidate before driver activation. Start with an empty
synthetic database; for a later approved restore, first complete the private
restore/egress isolation procedure and disable legacy routes on the copy. A
restored production database must not run background work against real payment
or OAuth services during this preparation. The helper does not establish that
isolation for you and is not a production cutover command.

Prepare a private copy of `bootstrap-route.example.json` outside Git with the
candidate's exact `/api/status` version, root administrator access token and
identity, approved model IDs/RealYu permission groups, and an existing Sub2API
standard group's ID. Never use a customer inference key as the administrator
credential. Create and authorize the Sub2API group using its own management UI
after the operator's required confirmation.

```powershell
python deploy/sub2api/bootstrap_route.py --private-config '<private directory>/bootstrap-route.json' --confirm-isolated-candidate
```

The helper permits only explicit `127.0.0.1` HTTP ports, rejects the old
production ports, checks the exact version and driver mode, and refuses active
legacy channels. It creates a non-secret placeholder route key, preserves
existing matching routes on rerun, and refuses to overwrite conflicts. A
network failure after creation is not retried as another create. The receipt
contains `channel_id`, `base_url` and `group_id`; put those values in the private
bindings file's `pools` array. The helper does not supply Sub2API credentials or
claim the selected upstream group is functional. Stop the candidate, select
`REALYU_UPSTREAM_DRIVER=sub2api`, then restart and run the complete acceptance
suite. The old channel APIs must now return 410.

Validate in the selected **Linux guest**, using a private, filled env file:

```sh
docker info --format '{{.OSType}}'
docker compose version
docker compose --env-file /private/realyu/candidate.env -f deploy/sub2api/compose.yaml config --quiet
```

`config --quiet` validates without printing secrets. Avoid plain `config`,
`docker inspect` or environment dumps in shared logs. The example pins registry
manifest digests checked on 2026-10-09. The Sub2API Git tag is `v0.2.15`; Docker Hub
uses `0.2.15` without `v`. Record actual platform/image IDs during acceptance; a
registry digest check alone does not establish that containers run correctly.
The Compose files have not themselves established compatibility with a Windows
Server Linux engine; that must be demonstrated on the target environment.

After the driver identity provisioning contract is configured and reviewed, an
isolated candidate may be started with the following explicit operator action:

```sh
docker compose --env-file /private/realyu/candidate.env -f deploy/sub2api/compose.yaml --profile portal up -d --build
docker compose --env-file /private/realyu/candidate.env -f deploy/sub2api/compose.yaml ps
```

These commands do not create public ingress. Do not use a copied production
database with unrestricted outbound network access before neutralizing only the
copy's scheduled payment jobs, credential refreshes and other background work.
Old and new systems must not independently refresh the same OAuth credentials.
Health checks prove HTTP readiness only; use the smoke acceptance matrix for
functional and billing verification.

Official configuration references (pinned to the researched Sub2API release):

- [Sub2API v0.2.15 deployment](https://github.com/Wei-Shaw/sub2api/tree/v0.2.15/deploy)
- [Sub2API v0.2.15 environment variables](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/deploy/.env.example)
- [Sub2API standard vs simple mode](https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/README_CN.md)

For a permanently revoked subject whose retained job blocks the queue: stop only
the candidate worker, verify RealYu access is revoked, archive that exact job
outside the scanned queue tree, retain progress for audit, then restart. Do not
archive an eligible subject to fabricate readiness; fix its supply configuration
and explicitly resume instead. Graceful interruption during projection retains
RUNNING/cooldown for idempotent restart recovery; permanent errors stay blocked.
