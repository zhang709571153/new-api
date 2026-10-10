# Username registration and personal wallet catalogue

Production execution is now complete: all six personal plans are for sale,
username registration/default personal Key and the model price page are enabled.
Merchant provider instances remain zero; wallet topups remain disabled. The first
run applied the intended settings and stopped on the native empty-list `null`
readback; the corrected run performed six plan PUTs and **zero repeated Settings
PUTs**. See the [retained receipts](../../lab/sub2api_e2e/ux-production-catalog-settings-20261010.json)
and [current release](UX-NATIVE-RELEASE-20261010.md). The development-only wording
below describes the tool's preparation, not the current deployment state.

`singlecore_ux_settings.py` prepares the normal admin API operation for
`realyu-singlecore-v0.2.15-20261010-ux`. This tool has not itself been run against
production during its development. The current deployment result belongs in
`UX-NATIVE-RELEASE-20261010.md` and the operation's private receipt.

The default command is fully offline. It reads only the reviewed, non-secret
`legacy-catalog-20261010.json` (fixed SHA-256 in the script). It does not read
credentials, contact the site, change settings, publish plans or create users.

```powershell
& 'C:\srv\realyu-singlecore-dev\runtime\venv\Scripts\python.exe' -B `
  'C:\Users\chunhei\.codex\handoff\realyu-sub2api-20261009\deploy\sub2api\singlecore_ux_settings.py'
```

The intended settings are the three username-registration fields, six
wallet/subscription fields and the model-price-page switch in `SETTINGS_PATCH`. New managed users
use username/password with optional email, and receive one personal Key in the
same user transaction, bound to explicit group 2. No welcome credit or default
subscription is added. Existing captcha, anti-abuse, branding and other settings
are preserved and compared after the PUT.

`payment_balance_disabled=true` disables **balance topups**. It does not disable
an existing wallet paying for a personal subscription. Payment provider
instances must be **zero**, including disabled instances. Merely disabling
visible payment methods or setting `payment_enabled_types=[]` is not a server
authorization boundary for merchant routes. This tool never imports, creates,
enables or sends a request to a merchant provider.

Native `payment_enabled_types` is a non-omitempty `[]string` response. Writing
`[]` stores an empty string; the read parser leaves the Go slice nil, so the
normal API returns a **present `null`**. For this field alone the helper treats
present `null` and `[]` as the same disabled state, both before deciding whether
to PUT and when checking the readback. Missing fields or nonempty values do not
match. All other settings and the zero-provider guard keep their strict checks.
The first production settings operation stopped on this representation mismatch
after its settings PUT and before plan publication; its original receipt stays
unchanged. A new operation can publish the reviewed plans without repeating that
already-applied Settings PUT.

Every run verifies group 2 (`RealYu Production OpenAI`, active, standard,
OpenAI, exclusive, multiplier 0.125), CNY display rate 7, and exactly the six
managed personal plans with source IDs 2–7. The exact names/prices/quotas come
from the reviewed catalogue: CNY 98/198/398/698/1398/2598, 28 days, total quota
four times weekly quota, balance payment allowed. Wallet prices must equal
`round(price_cents * 5000 / 7)` in integer quota. The group multiplier is not
applied again to the plan price. Native plan IDs may differ from source IDs.

Publishing is separate and explicit: `--publish-personal-plans` sends only
`{"for_sale":true}` to each normal plan PUT. It never edits entitlement metadata.
Without this flag, all existing sale states stay unchanged. Active-term repeat
purchases, renewal, upgrade, team creation and managed refunds remain outside
this operation. New registrations have zero balance; wallet purchase requires
an already funded wallet. Opening this flow does not verify merchant checkout,
topup or an external payment callback.

`model_plaza_enabled=true` opens the existing USD model price page for customers
with group access. The native service uses the existing channel pricing and
BillingService price schedules; the frontend applies the customer's/group's
multiplier and displays USD per million tokens. No model price is edited. The
read-only production inspection found one active channel bound to group 2 and
nine model-pricing rows. Group 2 remains exclusive: authorized logged-in users
can see its prices; anonymous visitors cannot see that group. Existing
`model_plaza_require_auth` and group visibility remain unchanged. The receipt
records only the authenticated caller's visible group/model counts, not a claim
that every user has the same access.

## OIDC default discrepancy

On this live release OIDC is disabled and the two stored security options are
false. Native `OIDCSecurityWriteDefaults` uses stored/default values while OIDC
is disabled; it does not apply the PUT's PKCE/ID-token pointers in that branch.
An unconfigured GET can display true before the first settings PUT writes the
compatibility false default. This is the reviewed upstream GET/write-default
discrepancy, not evidence that an enabled OIDC authentication route was used.

The tool requires the reconciled **disabled + false/false** state and explicitly
includes both values in its payload. It compares all Settings keys afterward,
and fails on any change outside the ten intended settings. It does not enable
OIDC, alter authentication code or write SQL to force a different result.

## Explicit execution

Use an exclusive operator window: do not concurrently change payment providers,
settings or plans. Normal APIs do not offer a transaction or compare-and-swap
across these resources. Repeated preflights narrow this race but cannot remove
it. This is an operational limitation, not a claim of atomic multi-API rollout.

The credential JSON contains the existing `admin_username` and `admin_password`.
Keep it and the new evidence directory outside Git. Never put credentials on the
command line. Set the two variables below to the already reviewed private files
and a **new** directory; an existing directory is rejected to retain first errors.

```powershell
# Set $PrivateAdminCredentials and $NewPrivateEvidenceDirectory privately.
& 'C:\srv\realyu-singlecore-dev\runtime\venv\Scripts\python.exe' -B `
  'C:\Users\chunhei\.codex\handoff\realyu-sub2api-20261009\deploy\sub2api\singlecore_ux_settings.py' `
  --execute --credentials $PrivateAdminCredentials `
  --output-dir $NewPrivateEvidenceDirectory --publish-personal-plans
```

The only allowed targets are `https://api.realyu.fun` (default) and the isolated
`http://127.0.0.1:29481` candidate. Both require the exact UX version. The target
status is checked before credentials are read. HTTPS uses system certificate
verification; ambient proxies and redirects are disabled. There are no retries;
the whole operation has a 180-second / 80-request budget. No model, user signup,
payment order or ledger write is part of the test tool.

`receipt.json` records the safe ten-field before/after intent, complete Settings
digests and changed key names; `http.jsonl` records only method/path/status,
request IDs, CF-Ray and elapsed time. It does not record passwords, tokens,
response bodies, provider configuration or customer information. The temporary
login refresh token is revoked on exit when returned by the login endpoint.

On any error, inspect the retained first failure and actual current state.
Settings or earlier plans may already be live; do not infer rollback from a
nonzero exit. There is no automatic rollback, retry, order cancellation or
funding mutation. After opening registration/new orders, preserve new data and
correct configuration forward. A separately reviewed run may resume because
all planned writes are idempotent, but it needs a fresh evidence directory.

## Offline validation

```powershell
Set-Location 'C:\Users\chunhei\.codex\handoff\realyu-sub2api-20261009\deploy\sub2api'
& 'C:\srv\realyu-singlecore-dev\runtime\venv\Scripts\python.exe' -B `
  -m unittest test_singlecore_ux_settings -v
```

Tests use synthetic API responses and no database or network. They cover the
default-offline boundary, precise settings/plan changes, zero-merchant guard,
catalogue and wallet-price mismatches, concurrent changes, partial publication,
first-error retention and no ambient proxy/redirect. They are not a production
registration or wallet-purchase E2E. Relevant security controls follow the
[OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
and [Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
guidance for authenticated admin actions, credential transport and keeping
credentials out of evidence; no broader ASVS certification is asserted.
