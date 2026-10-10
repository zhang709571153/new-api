# Native single-core public acceptance

[`singlecore_production_acceptance.py`](singlecore_production_acceptance.py) is
the bounded public test for the 2026-10-10 same-host deployment. It is offline by
default. It is not the old SQLite-ledger acceptance script and does not provision
test customers, change balances or retry failed model requests.

Copy it into the private operation runtime directory before use. Its authority
receipt is resolved relative to the script: `production-cutover-20261010/authority-receipt.json`.
The operator credential path and expected version are pinned in the source;
review those for another host. The output must be a new subdirectory of this
runtime. No private receipt, key, database or output belongs in Git.

```powershell
$python = 'C:\Users\chunhei\.codex\worktrees\realyu-sub2api\realyu-newapi-lab\.lab\sub2api-e2e\venv\Scripts\python.exe'
$script = 'C:\srv\realyu-singlecore-dev\runtime\production-singlecore-e2e.py'
# Offline only: no credential access, production file reads or HTTP requests.
& $python -B $script --output 'C:\srv\realyu-singlecore-dev\runtime\public-offline-unique' --self-test
# Explicit run after the deployment reports ACTIVE/opened.
& $python -B $script --output 'C:\srv\realyu-singlecore-dev\runtime\public-core-unique' --run
```

Default cases are the model catalog, Responses stream/nonstream, randomized
inline PDF extraction and a real web search with source citations: at most four
model requests. Before reading the operator API key, the script verifies both
the local ACTIVE/opened receipt and the public version/engine/single-core status.
It checks terminal responses, delta/final equality, actual tool items, returned
usage, CF-Ray/probe/request IDs and full response bodies. TLS verification stays
enabled, ambient proxies are ignored, and the default wall budget is 600 seconds.
There are no automatic retries; the first failure is retained and stops the run.

Optional cases: `image-generation,websocket,tool-roundtrip,all-text-models`.
Use `--cases` with that comma-separated list and a fresh output directory; these
allow at most 13 model requests. Set `--budget-seconds 1200` if needed. The sole
supported explicit proxy is `--proxy http://127.0.0.1:17897`; keep direct and proxy
results separate. Do not rerun just to hide a failure.

The image case requests one low-quality PNG of the user's Bichon/Genshin/hilichurl
scene through Responses `image_generation`, validates the real tool result and
fully decodes the PNG. A human/visual review must still verify the scene; this
does not test the separate native Images endpoint. WS checks two turns on the
same socket with `previous_response_id`; it does not prove crash-safe settlement.

Dependencies already verified locally: httpx 0.28.1, websockets 17.2 and Pillow
12.3.0. The script uses a fresh Windows ACL-restricted output directory. Console
output includes only fixed check codes/status; raw requests, response bodies,
images and detailed reports stay private. A total-budget exit can leave an
in-flight request requiring a separate accounting review; no automatic refund
or balance correction is performed.

Required separate acceptance: PostgreSQL funding/usage reconciliation and payer/
actor/team attribution, old API-key and disabled-key behavior, cross-subject
denials, same-client CLI continuation, browser login/team/admin pages, actual
SG path and sample freshness. Reboot/no-login recovery and long-term stability
are not inferred from these model requests.

On 2026-10-10 the script passed 20 offline contracts and four invalid-command
checks. The public `--run` phase remained pending final core activation. A
separate actual old-public CLI turn completed with an exact reply and exit 0;
its local `gpt-6-luna` fallback-metadata warning was retained, and its conversation
was saved privately for post-cutover resume. This is not a claim that native
public acceptance or cross-cutover resume has already passed.
