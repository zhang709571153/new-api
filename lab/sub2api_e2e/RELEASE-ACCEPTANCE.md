# Release acceptance after deployment authorization

This is an execution plan, not a claim of completed production acceptance.
Keep previous first-failure artifacts and their binary hashes unchanged.

## Candidate preparation

- Hash the exact release gateway and worker. Start a copy of the preparation
  snapshot, never the preparation snapshot or live customer database.
- Before the Sub2API driver is configured, run the clone as `NODE_TYPE=slave`.
  Clear `CHANNEL_UPDATE_FREQUENCY` and inherited connection/driver variables;
  do not start legacy launchers or credential watchers. Disable old channels
  through the clone's official management API before creating the type-59 route.
- Use a separate clone namespace and dedicated operator accounts. The minimum
  identity set is personal A, personal B and B as a member of A's test team.
  Prewarm only these selected identities; do not use the production namespace.
- Keep the inherited model pricing. Test wallets and subscriptions are synthetic
  clone state. Production smoke must use dedicated operator funds and identities.
- Configure the gateway's PDF-download exit separately from Sub2API's upstream
  proxy. Preserve TLS verification. Explicit local proxy settings need an exact
  `NO_PROXY` for the local gateway and Sub2API.

## Bounded actual requests

`release_acceptance.py` validates local inputs without network requests unless
`--run` is specified. The private JSON contains:

```json
{
  "base_url": "http://127.0.0.1:28601/v1",
  "expected_version": "realyu-sub2api-v3.10.0-20261009",
  "gateway_binary": "PRIVATE_ABSOLUTE_PATH",
  "gateway_sha256": "EXPECTED_SHA256",
  "ledger_db": "PRIVATE_ABSOLUTE_SQLITE_PATH",
  "dedicated_operator_subjects": true,
  "model": "gpt-6.1-sol",
  "subjects": [
    {"label":"personal-a","api_key":"PRIVATE","user_id":101,"billing_owner_id":101,"workspace_team_id":0,"token_id":201},
    {"label":"personal-b","api_key":"PRIVATE","user_id":102,"billing_owner_id":102,"workspace_team_id":0,"token_id":202},
    {"label":"team-b","api_key":"PRIVATE","user_id":102,"billing_owner_id":101,"workspace_team_id":301,"token_id":203}
  ]
}
```

Use a new private output directory every time:

```powershell
python lab/sub2api_e2e/release_acceptance.py --secret-file PRIVATE_JSON --output NEW_PRIVATE_OUTPUT
python lab/sub2api_e2e/release_acceptance.py --secret-file PRIVATE_JSON --output NEW_PRIVATE_OUTPUT --run
```

The default three-subject/eight-text-model run allows at most 23 inference
requests, including two turns for function tools and two WS turns. Requests
are sequential, with no automatic retries. Cases can be selected explicitly
using `--cases`; a rerun is a new observation, not a repair of a first failure.

| Check | Required evidence |
| --- | --- |
| Models | All eight text models and `gpt-image-2` appear; model detail equals its list object; unknown model is HTTP 404 |
| SSE / SDK | Terminal `completed`, full final output, delta text equals SDK final text; HTTP 200 alone is insufficient |
| Every text model | One exact short marker for each model; every final body completes |
| Inline PDF | Both pages: verification code, count 43 and warehouse name |
| PDF URL | Actual W3 PDF content; no TLS-verification bypass |
| Web search | Actual `web_search_call` and final `url_citation`, not just a URL in text |
| Function tools | Completed function call and completed response after the tool result |
| WS continuation | Same socket, two completed turns, `previous_response_id` and remembered marker |
| Images API | Exactly one decodable image; verify the saved image visually; record actual dimensions separately |
| Subjects | Individual successful final responses and independently attributed usage for the three identities |
| Failed request | Record bounded validation denial and zero balance change; this alone does not prove reserved-quota refund |

The runner's ledger access is read-only and validates each configured token
against its declared member and payer. It records only the selected operator
identities. Final exact settlement must also be reconciled after asynchronous
usage logs finish: one log per request, wallet or subscription funding owner,
member allowance and personal wallet preservation. A naturally failed request
needs matching reservation/refund server logs to establish a refund.

## Separate mandatory evidence

Do not label the positive three-subject smoke as cross-tenant isolation. Use the
existing `stock_chain_acceptance.py` with the final binary and a dedicated stock
Sub2API/mock pool for same-owner continuation versus other member/personal-owner
denials, exact subscription/wallet accounting, interruption/refund and restart.
Do not inject upstream failures into production account pools.

Verify the selected subjects have distinct stock Sub2API user IDs and that the
team member's projected identity uses the member plus team, while the customer
charge still belongs to the payer. Verify readiness and internal credit headroom
before admission. Worker failure or missing mappings must fail closed without
customer-path login or account creation.

The latest native Codex 0.162.0 needs a fresh isolated home test against the final
edge: simple dialogue, process resume, and a tool turn with full terminal output.
Do not change local execution policy to force tool acceptance. Preserve policy
blocks separately from the previously observed HTTP/2 stream failure.

For explicitly authorized production smoke, `prepare_operator_fixture.py`
creates two new `realyu_release_...` users through normal administrative APIs,
then creates the personal and team keys through dashboard sessions. It never
sends inference, purchases a plan, changes an existing user, or edits a channel.
Keep its output private. The owner receives 250,000 wallet quota and a team
weekly allowance of 250,000; the member receives 500,000 wallet quota. The
official team provisioning API creates its normal four-week subscription.
The lead must prewarm these identities under the production namespace and
confirm cutover before adding the final gateway SHA/version to that fixture.

`release_cli_dialogue.py` performs the narrower production CLI check requested
for release: one no-tool dialogue and a separate-process resume. It requires
exact replies, `turn.completed`, zero tool items and no stream failure. Its
new `CODEX_HOME` does not alter daily Codex credentials or execution policy.
This does not count as a successful file/tool or desktop UI test.

`release_owner_isolation.py` holds the source person's WS open, attempts its
response ID from another person and from the same person's team identity over
both HTTP and WS, and also rejects unsigned/tampered HTTP references. It then
requires successful continuation on the original connection. All six denied
requests must expose no retained content and change no subject's token balance.
The eight attempts are bounded with runner retries disabled; upstream internal
retry behavior is separate. A failure remains a first-failure artifact.

The production first attempt exposed a missing ownership boundary in stock
upstream retained-response routing. A mocked provider's denial was insufficient
evidence. The gateway now authenticates opaque response IDs with its immutable
identity secret, namespace, member and team before any upstream lookup. Keep
this real-provider isolation test in the release checklist. Unauthenticated raw
response IDs from older releases and server-retained `conversation` or
`item_reference` lookups are rejected. Full inline history remains supported.

```powershell
python lab/sub2api_e2e/release_cli_dialogue.py --codex PINNED_CODEX_EXE --secret-file PRIVATE_JSON --output NEW_PRIVATE_OUTPUT --run
python lab/sub2api_e2e/release_owner_isolation.py --secret-file PRIVATE_JSON --output NEW_PRIVATE_OUTPUT --run
```

Both commands validate locally without making network requests when `--run`
is omitted. Disable only the dedicated operator users and personal keys after
final acceptance, retaining financial and management audit history. Workspace
keys reject generic token changes; disable the dedicated users and require all
four keys, including team-owner and member keys, to fail authentication. Do not
delete a team or alter its balances merely to clean up an acceptance fixture.

## Cutover and rollback observations

Before cutover, complete the full candidate chain through the new edge, confirm
the exact SHA/version, and preserve the rollback program, live data backup and
single-owner OAuth transition records. The lead executor controls cutover.

After cutover, verify public health and assets, unauthenticated rejection,
catalog consistency, one complete public SSE, inline PDF, PDF URL, cited search,
WS continuation and one image on dedicated operator credentials. Reconcile
request IDs to origin logs and the dedicated ledger. This verifies a short
sample, not a long-term SLA. A failed terminal stream, customer-data regression,
cross-owner exposure or financial invariant failure requires stopping admission
and invoking the lead's prepared rollback procedure.

Website browser UI, native desktop UI, service-manager recovery, system reboot
and sustained load remain separate checks. Do not substitute protocol or
loopback health results for them.
