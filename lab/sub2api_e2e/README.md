# Sub2API candidate acceptance

These tools target isolated candidates. They do not publish RealYu, migrate a
production database, import production refresh tokens, or change everyday Codex
configuration. Generated credentials, logs, downloads, DB data and process
receipts belong in ignored `.lab/sub2api-e2e/` and must not be committed.

## Isolated Windows runtime

`bootstrap_windows.py --pg-bin <existing PostgreSQL bin directory>` downloads
Sub2API **v0.2.15** and verifies its published Windows ZIP SHA256. It separately
verifies the **Redis 8.10.2 community Windows MSYS2 build**; that build is for this
test host only, not a production deployment recommendation. PostgreSQL is started
with a new data directory, SCRAM credentials and loopback binding. No SCM service,
WSL distribution, container engine or system installation is changed.

Default ports: Sub2API **28082**, PostgreSQL **28432**, Redis **28379**, mock
upstream **28083**. The script refuses occupied ports. It stores owned PIDs and
exact executable paths in the private credentials receipt. Stop only processes
from this receipt after verifying PID identity; use `pg_ctl -D <this run's data
directory> -m fast stop` for PostgreSQL. Never kill by executable name.

The stock console requires the deploying party's electronic acknowledgment of
`docs/legal/admin-compliance.*.md`. `provision_mock.py` reads the actual status and
returns `BLOCKED / ADMIN_COMPLIANCE_ACK_REQUIRED` if unacknowledged. It does not
accept commitments, bypass middleware, or synthesize acknowledgment records.
After the operator has completed that step, it creates two exclusive standard
OpenAI groups and two mock upstream accounts, and saves a system management key
privately. Production commitments are not inferred from consent to local testing.

Set `token_refresh.enabled=false` for the candidate. That only disables the
background service: the request path can also refresh OAuth. For bounded real
upstream tests, omit refresh tokens entirely and preserve the actual unexpired
access-token expiry. An expired credential blocks the test; never alter expiry
to pretend the token is valid.

`restart_check_windows.py` verifies the owned Sub2API PID's executable path before
restarting that process and checks persistent administrator identity. This has
been run twice. It does not install/test an SCM service, reboot the machine, or
establish unattended process supervision; those remain deployment acceptance.

## Separate evidence layers

1. `mock_upstream.py --port 28083`: deterministic upstream. Its `/observations`
   endpoint records synthetic requests, selected identity hash and concurrency.
   `MOCK_HOLD` waits for `/control/release`; `MOCK_FAIL_429`/`MOCK_FAIL_500` inject
   failures; `MOCK_EMPTY_TERMINAL` deliberately emits deltas and done items but an
   empty final object. All endpoints bind only to loopback.
2. Actual RealYu + Sub2API + PostgreSQL + Redis, with only the upstream mocked:
   verify per-user provisioning, pool isolation, headers, protocol, limits,
   cancellation, exact usage and ledger settlement. A passing mocked chain does
   not establish PDF understanding, real search, image quality or OAuth refresh.
3. Real upstream: run the portable API suite and official SDK suite against the
   candidate, using an isolated internal user, synthetic files and no retries.
   Preserve every first failure. Repeat attempts write a new result directory.
4. Actual Codex CLI and desktop are separate acceptance scopes. Use a clean
   `CODEX_HOME`, the verified current CLI binary and the supplied `codex-fixture`.
   File changes, tests, restart/resume and long-context compaction must be real.
   CLI/app-server success does not establish desktop preview or UI success.

## API runner

`portable/` reuses the existing 2026-10-09 RealYu acceptance suite and synthetic
PDF/PNG/Codex fixtures. The terminal-output check has been strengthened: empty
`response.completed.output` is now **FAIL**, even when preceding deltas are right.
The 67-item checklist is a specification, not a prefilled acceptance report.

```text
python portable/smoke_api.py --suite api --base-url http://127.0.0.1:PORT/v1 --network direct --output PRIVATE/first-api
python portable/smoke_api.py --suite images --base-url http://127.0.0.1:PORT/v1 --network direct --output PRIVATE/first-images
python strict_sdk.py --base-url http://127.0.0.1:PORT/v1 --upstream real --extended --output PRIVATE/first-sdk.json
```

Supply `REALYU_API_KEY` only through the child process environment. Do not put
keys in CLI arguments, report bodies or repository files. Install the pinned
`requirements.txt` into an isolated venv for `strict_sdk.py`; the portable suite
needs only Python's standard library. `--upstream` explicitly distinguishes real
from mocked evidence; external endpoint testing also requires `--allow-external`.

`strict_sdk.py` checks actual official SDK `get_final_response()`, delta/final
equality, terminal state and preservation of completed item IDs. When run directly
against the mock, three normal cases must pass and the deliberately malformed
terminal case must fail. This intentional failure proves the runner rejects the
original incident; it is not a passing Sub2API integration test.

Images require visual review, and precise dimensions/options need independent
checks. Files API, stateful response-ID continuation, legacy compact and current
Codex compact v2 are different capabilities. Unsupported/blocked/not-run cases
must remain explicit; no-count skips never count as success.

## Acceptance gate

Before publication, require complete candidate-chain evidence for protocol,
official SDK PDF/search finals, JSON and multipart image editing, real CLI
read/change/test/resume/compact, separate internal identities, dedicated-pool
isolation, one customer settlement, cancellation and retry accounting, and
bounded concurrency recovery. If database behavior changes, use real SQLite,
MySQL and PostgreSQL on disposable databases, including fresh and upgraded state
and two restarts. Do not infer stability/SLA from a small sample or mock loop.

## Candidate negative-path and database evidence

`realyu_negative.py --binary <candidate.exe>` bootstraps a fresh loopback RealYu
instance, uses the deployment route bootstrap before enabling Sub2API mode, and
then checks disabled legacy management APIs, per-customer safe failures, catalog
failure closure and unchanged wallet/key/subscription counters. A deliberately
higher-priority legacy mock route must remain unused. This is actual RealYu
plus Sub2API rejection evidence, not successful inference. Its current stock
Sub2API rejection is HTTP 401 for an invalid management key; the administrator
JWT's compliance HTTP 423 is verified separately. Neither is substituted for a
successful model call. The deployment bootstrap is exercised through an existing
authenticated fixture adapter. A separate `--bootstrap-only` run also verifies
the standalone deployment `Client` over real Bearer HTTP: create a disposable
administrator token using `/api/verify` password proof, perform idempotent route
readback, revoke it with a fresh proof and check `token/status.exists=false`.
The proof requirement is retained; no token or authorization row is seeded.

`--team-scope` uses the existing administrator team-provisioning API to grant
synthetic service credit to a new fixture, with no payment order or wallet
transfer. Two members share the payer and retain distinct authenticated member
IDs. Selected failure cases reconcile team/personal wallets, key counters,
subscription use, member weekly use and pending reservations, then check leaving
the team revokes the former team key and restores personal scope. These rejection
checks cannot prove successful upstream identity isolation; the actual Sub2API
success chain and retained-response ownership tests remain separate gates.

`database_regression.py` verifies the exact frozen baseline executable SHA256
before creating any synthetic data. It reuses the repository's real database
matrix for fresh startup, baseline-to-candidate upgrade, repeated startup,
wallet/keys/ledger preservation and three team/funding regressions. Each test
machine should pass `--go <verified-go.exe>` along with `--engines-root`; the
runner also accepts Go from PATH. It fails before starting fixtures when no
toolchain is available, rather than requiring the development host's path. Each test
root gets its own empty database. MySQL/PostgreSQL fixture reuse initially failed
the suite's empty-database guard; the first result is retained alongside the
corrected run. No production database is read or copied. This tests installed
engine versions, not minimum-supported MySQL/PostgreSQL versions.

Current sanitized evidence is in `negative-summary.json` (35 actual HTTP and
ledger checks), `bootstrap-summary.json` (5 route/bootstrap/cleanup checks), and
`database-summary.json` (6 fresh/upgrade scenarios, 2 candidate startups each,
plus 9 team/funding roots across the three engines, without skips). The SQLite
startup/upgrade scenarios use a separate MySQL log database; the database report
records each primary/log engine pairing. Binary hashes identify exactly what was
tested. `validation-summary.json` keeps the passing evidence layers separate
from outstanding product failures and untested client/deployment behavior.

`team-negative-summary.json` records 38 passing checks against the newer v3
binary after the authenticated-member/workspace subject correction. These cover
the actual shared-payer fixture, safe failures and closed Codex catalogs for
three team keys, three concurrent rejections without residual billing changes,
team departure/key revocation and personal-scope restoration. Earlier v2 results
are retained with their own hashes. The schema matrix was not rerun for this
subject-only change; no new database migration was introduced.

## Prewarming worker contract acceptance

`prewarm_worker_acceptance.py --worker <prewarm.exe> --gateway <candidate.exe>`
starts only suite-owned short processes against `projection_control_fixture.py`,
a separate loopback fake of the Sub2API management protocol. Its scope is worker
scheduling, persistent progress, identity ownership, safe errors and the actual
RealYu HTTP rejection/ledger path. It cannot establish stock Sub2API compatibility
or real inference. All inputs, state and process logs remain private.

The runner covers multiple subjects and pools, real spacing between a small
number of logins, restart/resume, loss of local progress with remote recovery,
429 without waiting a minute, 423 queue stop, and a gateway `prewarmed` miss that
must perform no account creation, login or inference. A retry during the saved
backoff must return without sending management requests. Advancing the clock
beyond the delay and higher-volume limiter behavior belong in controlled-clock
unit tests, not an accelerated production CLI mode or lengthy mock load loop.

## Actual stock-service and real-upstream evidence

The operator completed the stock administrator acknowledgment after explicit
confirmation. `stock-chain-summary.json` records 21 passing checks through the
actual Sub2API v0.2.15 process, PostgreSQL and Redis, with only its upstream mocked:
two distinct users, two dedicated pool keys each, official SDK streaming/final
Responses and Chat, exact six-request billing reconciliation and gateway restart
recovery. A first mock deficiency is retained: stock account capability probing
required a function-call response, and Chat streaming needed proper SSE. After
repair, official account updates performed real probes which reported native
Responses support. Capability flags were not synthesized.

`real-chain-sdk-summary.json` records five passing real upstream cases through
the isolated RealYu v4 candidate: SSE, nonstream, Chat, two-page PDF and forced
web search with final citations. `real-chain-features-summary.json` records real
function roundtrip, image input, JSON schema, native compaction v2 replay,
same-socket two-turn WS continuation and native image generation. These reports
do not establish desktop UI acceptance, transport reconnect, cancellation
accounting, long-duration stability or service supervision.

`stock-team-first-failure.json` preserves an actual v4 failure: the ninth
serial team request returned SQLite `SQLITE_BUSY` during token pre-consumption
while the preceding request finished its ledger write. The prior eight requests
reconcile exactly (880 quota in subscription, tokens, member weekly usage and
eight settled records; no personal-wallet debit). Investigation found that the
harness supplied a bare database path, overriding the application's safe default
SQLite DSN. The corrected runner uses the installed glebarez driver's supported
`_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate` options.
The deployment template was corrected to use those supported options as well.
`stock-team-summary.json` records the subsequent passing continuous-request,
per-member billing and cross-subject response-ID rejection checks. Requests have
no added delay or SDK retry. Only the final asynchronous ledger observation has
a bounded five-second completion window. This is sampled acceptance, not an SLA.

`enrollment_watch_acceptance.py --gateway <candidate.exe> --worker <worker.exe>`
uses fresh customer data and actual gateway, worker and stock Sub2API processes.
Its initial jobs must appear from the background scanner before any status-page
request. It verifies a missing worker is visible, unprepared HTTP requests have
no model execution or debit, personal/team scopes become independently ready,
and a prepared member can use the team subscription. It then registers a new
customer and joins that customer to a team while both processes remain active:
the scanner must discover each change without manual queue files or status polls.
`enrollment-watch-summary.json` records 22 passing checks including independent
worker and gateway restarts, no duplicate upstream users and no duplicate debit.
The three-engine `enrollment-database-summary.json` additionally tests the union
of permitted native-key pools, disabled/expired keys, auto groups and revocation.

`portable-api-summary.json` preserves the broader real-upstream API matrix:
23 passing checks, 8 failing checks and 3 models not offered by the isolated
pool. Both Chat and Responses function roundtrips, JSON schemas, image reading
and PDF inputs pass; Messages-compatible requests execute the GPT model, and
standalone search returns actual results. These findings have explicit limits:

- Model listing and inference work, but the offered model's detail endpoint
  returns HTTP 200 containing `model_not_found`. This is an unresolved catalog
  inconsistency in v4. The v7 repair and targeted HTTP acceptance are recorded
  separately in `model-catalog-summary.json`; the original failure is retained.
- The OAuth upstream rejects HTTP `previous_response_id`; successful real WS
  continuation and the API-key mock ownership test are separate evidence.
- Legacy `/responses/compact` fails; the separately tested native compaction v2
  path passes. `web_search_preview` is unsupported, while `web_search` passes.
- Files upload is rejected before any `file_id` exists. Public PDF URL download
  initially failed certificate validation through the gateway's direct egress.
  `pdf-url-egress-summary.json` records one successful request after explicitly
  configuring gateway HTTP(S) proxy and local-address `NO_PROXY`; the PDF text
  was exact. The Sub2API account's proxy alone does not configure gateway file
  downloads. Certificate verification was not bypassed.
- Chat with web-search options returns text and a Markdown link, but no search
  execution or structured citation proof. Its strict search assertion remains
  failed. Standalone search has not been followed by a second model answer.
- Embeddings, TTS speech and rerank models are not offered by this pool; legacy
  completions returns 404. Other audio operations were not exercised.

The API matrix only inventories the older CLI found on `PATH`; it does not run
that executable. Actual CLI evidence includes the bundled 0.160.0 binary and
the separately downloaded official latest stable 0.162.0 binary below.
The current 67-item snapshot contains 24 `PASS`, 9 `PARTIAL`, 4 `FAIL`,
2 `BLOCKED` and 28 `NOT_RUN`; endpoint results are not equivalent to completing
all native Codex acceptance criteria.

`model_catalog_acceptance.py --gateway <candidate.exe>` runs the actual gateway
with fresh zero-balance customers and a loopback request observer. Its 15 passing
v7 checks, repeated on the frozen-source v8 binary, prove model-list/detail
object agreement for custom names, token and
group restrictions, unknown-model 404, authentication enforcement and continued
inference rejection for a depleted key. Every ledger counter is unchanged and
the observer receives no management or inference requests. This read-only fix
does not silently replace earlier real-upstream evidence with a new binary.

`legacy-images-summary.json` records exactly three real v7 requests: one image
generation, multipart editing and JSON `images` editing. The PNGs decode fully;
visual comparison confirms the same scene is retained and a red mug is added.
All three customer log rows, wallet debit and key usage reconcile to 30,000
synthetic quota. Requested 1024x1024 dimensions were not honored: all outputs
are 1370x1148, so `IMG05` remains failed. No further image requests were made.

`credit-watch-summary.json` records 10 passing actual worker/stock-service
checks for internal allowance maintenance: one subject with two pool keys starts
at balance 1, receives a single additive 100 allowance and retains balance 101
after worker restart. The official balance history contains one matching
operation audit. This test does not invoke models or touch any RealYu customer
database, wallet or payment; earlier whole-chain tests retain their own hashes.

`browser-ui-summary.json` records the website browser runtime crashing before
page content or login could be read. It is a tool/runtime blockage, not product
UI acceptance. `acceptance-gaps.json` enumerates the 24 remaining P0 acceptance
gaps and distinguishes them from proven failures. Native custom tools, complete
desktop coding, cancellation accounting, transport fallback/reconnect and
target-host service/reboot acceptance remain incomplete. The final v8 gateway
has targeted catalog evidence; v4 real SDK/features and v6 enrollment/team
results must not be relabeled as a complete v8 regression.

Independent persistence evidence is linked from
`deploy/sub2api/native-persistence-validation.json`: fresh PostgreSQL and Redis
fixtures pass 12 checks including database restore, Redis AOF/RDB recovery and
an owned Redis process crash. This separate fixture does not establish SCM,
system reboot, target-machine, disk-full or long-duration recovery behavior.

`native-cli-summary.json` records actual Codex CLI 0.160.0 conversation and
new-process resume through RealYu. The local execution policy rejected file and
terminal tools; that workflow is `BLOCKED`, not a passing coding workflow or an
API protocol failure. Daily Codex configuration was not changed, and the policy
was not bypassed. Desktop UI, client compact and remaining native tools require
separate evidence.

`checklist-current.json` / `.csv` are the evidence-filled 67-item snapshot for
this candidate. Combined criteria remain `PARTIAL` when only one protocol or
client surface was tested; `NOT_RUN` is not success. The portable checklist stays
blank for another machine's independent run. Reports carry exact candidate hashes
so later revisions cannot silently inherit earlier acceptance.

`native-cli-latest-summary.json` records the official latest stable CLI 0.162.0
(published 2026-10-08; official ZIP digest verified). The first coding workflow
hit both local tool-policy refusal and a separate upstream HTTP/2 INTERNAL_ERROR
before its terminal response. `latest-cli-stream-failure.json` correlates the
request through RealYu and Sub2API: failed quota zero, full 16527 precharge refund,
and no Sub2API usage row for the failed call. The upstream endpoint versus the
egress proxy remains undetermined. An independent no-tool dialogue and
new-process marker resume completed; neither overwrites the first failure.
