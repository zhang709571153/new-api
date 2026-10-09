---
name: realyu-maintenance
description: >-
  Maintain the local Realyu API deployment and its New API integration workspace.
  Use for api.realyu.fun operations, Codex account pools and fallback, API keys
  and teams, RMB billing and subscriptions, native images, client setup scripts,
  release troubleshooting, or onboarding to this host's Realyu customizations.
  Routes to existing module docs and upstream skills rather than introducing
  another deployment or approval workflow.
---

# Realyu 系统维护

Use this as a navigation skill. Continue authorized work directly; this skill adds
no permission prompts, mandatory pauses, release gates, or fixed full-suite run.

## Locate the right tree

For a checkout containing `HANDOFF-SUB2API.md`, follow that candidate handoff and
`lab/maintenance/sub2api-migration.md` first. Resolve paths from the checkout;
do not use historical host paths on another machine. Production stays unchanged
until the user explicitly authorizes cutover.

The remaining historical-tree navigation applies only to checkouts without
`HANDOFF-SUB2API.md`. The clean handoff deliberately excludes old private host
runbooks; their absence is not a reason to copy files from an unrelated machine.

Find the repository containing `lab/maintenance/README.md`. On this host the
integration tree was a host-local `realyu-release-migration` worktree.
The common starting directory `C:/srv/realyu-newapi-lab` is the older pilot.
Use `git worktree list` if those paths change. A worktree checkout, a compiled
executable, and the live release directory are distinct; Git HEAD alone may not
describe the running build.

Read `lab/maintenance/README.md` in that repository, then only the relevant module:

| Task | Read relative to repository root |
| --- | --- |
| Process, tunnel, deployment, rollback | `lab/maintenance/runtime.md` |
| OAuth, account routing, fallback, cache, images | `lab/maintenance/relay-pool.md` |
| Pricing, RMB display, usage, weekly/monthly limits, payment | `lab/maintenance/billing.md` |
| Registration, persistent Key, teams, roles, nickname | `lab/maintenance/workspace.md` |
| Homepage, dashboard, platform setup, downloads | `lab/maintenance/frontend-client.md` |
| Error diagnosis and existing verification tools | `lab/maintenance/verification.md` |

## Apply the existing implementation

- Follow links to the specific source boundary; avoid rereading the whole repo.
- Reuse the repository's shadcn-ui, i18n-translate and React skills as applicable.
  Keep existing AGENTS and upstream billing/plugin documentation authoritative
  for their topics; don't duplicate their workflows here.
- For live status, optionally run `lab/maintenance/snapshot.ps1`; it discovers
  the scheduled-task release target and reads allowlisted status, without
  restarting services, changing configuration or consuming model credits.
- Treat dated RELEASE/research/results files as historical evidence. Verify
  current configuration when a decision depends on it. Source support,
  installed client behavior, and end-to-end acceptance are separate facts.
- Keep account OAuth credentials and downstream Keys out of documents and
  diagnostic output. Request IDs and usage/category metadata usually suffice.
- Select existing tests for the affected behavior. Some lab scripts mutate
  channels or run paid image requests; their module docs identify the scope.
  Don't run all historical scripts merely to onboard.
- Update the relevant module note when behavior or paths change, preserving
  dated acceptance records. Docs-only edits do not need a production restart.

## Important current distinctions

Quota is monetary storage, not Token count; RMB display is not a second charge.
A team member limit does not create funds. Pool percentages are upstream usage
windows, not API dollars. Gateway native image support does not prove every
installed client uses native tools. An HTTP 429 is not always account exhaustion.
Runtime restart can lose current in-memory affinity and cool the prompt cache.
