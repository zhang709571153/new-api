# Retained response ownership

The first public Sub2API cutover on 2026-10-10 was rolled back after a real
cross-subject WS continuation selected a retained upstream connection and timed
out with a busy error. No foreign text or charge was observed, but this was not
an authoritative ownership denial. The first failure remains recorded; stock
mock acceptance did not establish this security boundary.

The `v3.10.0.1` gateway exposes opaque authenticated `resp_ry1_` response IDs.
The signature binds the raw upstream ID to the immutable namespace, identity
secret, authenticated member and workspace team. HTTP/SSE and WS responses use
the same representation. A verified owner reference is decoded only while
forwarding upstream. Foreign, modified or unsigned references receive HTTP/WS
403 without an upstream retained-response lookup. WS cancellation references
use the same check. Billing owner identity does not replace the actual member.

This stateless check survives gateway restarts and multiple gateway processes
sharing the same private bindings. Preserve namespace and identity secret when
migrating; rotating either invalidates prior response references. The envelope
is authentication, not encryption, and the raw ID alone grants no gateway access.
No customer database migration or price change is required.

Clients should treat IDs as opaque. Raw IDs returned by pre-fix gateways cannot
be used for retained continuation after this change; resend inline history or
start a new response. Server-retained `conversation` and `item_reference` input
lookups are denied because their ownership is not authenticated. Full inline
history, item IDs, encrypted reasoning, usage, explicit zeros and unknown JSON
fields remain preserved. This is a compatibility boundary, not complete native
Codex feature equivalence.

Acceptance includes service protocol tests, real HTTP/SSE handlers, real-provider
same-owner WS continuation, cross-person and personal-versus-team denial over
HTTP and WS, tampered/raw ID rejection, zero denied-request charges, and native
Codex 0.162.0 dialogue/resume. See `release-scope-candidate-20261010.json` for the
exact candidate binary; production requires its own post-cutover receipt.
