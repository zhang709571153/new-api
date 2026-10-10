# SG-HY2 independent Windows services — candidate, not deployed

Prepared 2026-10-10. These scripts add independent network services; they do not
perform the single-core application/database migration. No credentials belong in
Git. Installation and production route changes are separate explicit commands.

## Current findings and release boundary

At 18:18:57 Beijing, the isolated SG-HY2 candidate Tunnel lost all four edge
connections together during a 120-second SSE test. Proxy-side SSE ended after
70.406 seconds with only events 0–6; direct-side SSE timed out after 76.437 seconds.
All four edges re-registered at 18:19:01. The fixture was configured for 13 events
at 10-second intervals and its fault injection was scheduled after the 65-minute
soak; it did not deliberately end this stream at 70 seconds.

The current 7897 Mihomo core is a Clash Verge desktop sidecar, not an SCM service.
Its PID and parent remained those started at 11:39. The subsequently flushed
sidecar log contains the original four SG-HY2 edge flows and their simultaneous
18:19:00 replacement, but no corresponding reload/restart/QUIC error. There is no
GOST log for that candidate. No local authorized SSH entry for the separate SG
Windows server was found in the VPN project or its recent thread. Server-side
restart, shared QUIC-session reset, GOST reset and edge control failure are not
distinguished by the available evidence.

First-failure evidence remains under
`C:\srv\realyu-observability\incidents\sg-hy2-cutover-20261010-181714`.
This package addresses desktop-session dependence and adds persistent component
logs. It does **not** claim to fix or clear that network acceptance failure.
The root release operator must decide the network acceptance gate using complete
streams and real proxy-path evidence before applying either rolling stage.

A separate headless candidate later passed its **first** direct and explicit-proxy
120-second streams (13/13 events each), six complete-body checks and four parallel
requests. During 25 five-second samples all four edge flow IDs stayed unchanged
with positive traffic deltas. Its processes, DNS and Tunnel were removed; the
initial delayed cloud-cleanup failure is retained. See
[the sanitized validation record](SG-HY2-VALIDATION-20261010.json). This short test
uses a different Mihomo binary and cannot uniquely identify the earlier cause or
replace a long-duration or SCM boot/recovery test.

## Layout and invariants

| Component | Identity / binding | Lifetime |
|---|---|---|
| Mihomo | `RealYuSgHy2`, mixed `127.0.0.1:17897`, controller `127.0.0.1:17898` | Automatic delayed SCM, LocalService |
| GOST | `RealYuSgHy2EdgeProxy`, `127.0.0.1:19464–19467` | Automatic delayed SCM, depends on Mihomo |
| Existing GOST | `RealYuEdgeProxy`, `19454–19457` through `7890` | Kept running for old connectors and rollback |
| Desktop Verge | `7897` | Input configuration only; not modified or depended upon |

Install root: `C:\ProgramData\RealYuNetwork\sg-hy2-20261010`.
Only the existing `RealYu-SG-HY2` node is copied. Its endpoint, TLS SNI, certificate
fingerprint and `skip-cert-verify: false` are retained. No provider subscription,
desktop selection store, alternative route or DIRECT fallback is copied. TUN is
disabled and all listeners bind loopback. A new random controller secret is stored
only in the private config. LocalService has read/execute access to binaries and
configuration and modify access only to `state` and `logs`. LocalService is shared
with other services on this host; this is not a per-service credential boundary.

The Mihomo binary is the official v1.19.32 artifact already used by the isolated
VPN tests, SHA256 `0b54ea7b10e26f6ca628b49e77c13ad9b2587b23393a5b715769cbaeed012af6`.
The current WinSW 2.12.0 and GOST 3.3.0 binaries are copied and hashed in the private
plan. Core/GOST stdout and stderr use WinSW size rotation, 10 MiB × 8 per log.

## Prepare and validate

`sg_hy2_package.py` requires Python with PyYAML only while preparing. Services have
no Python, user-profile, UI, download-at-startup or repository dependency.
The recorded unit run used private `test-deps` with PyYAML 6.0.3; do not install
preparation dependencies into the production service interpreter.

```powershell
$OperatorSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Run from deploy/sub2api. Supply the actual trusted local executables.
& $Python -B .\sg_hy2_package.py `
  --source-profile $DesktopProfile --mihomo $Mihomo --winsw $WinSW --gost $Gost `
  --operation-root $PrivateOperationRoot `
  --install-root 'C:\ProgramData\RealYuNetwork\sg-hy2-20261010' `
  --manifest 'C:\ProgramData\RealYuServices\manifest.json' `
  --guard-config 'C:\ProgramData\RealYuTunnelGuard\config.json' `
  --operator-sid $OperatorSid
```

Use a new private operation directory outside Git. Preparation fails on occupied
candidate ports, a pre-existing installation, an unrecognized node shape, a
different pinned Mihomo binary, or changed production edge targets. The directory
ACL is restricted before any credential is written. Review the returned plan hash
and all script/file hashes; any later script edit requires a freshly reviewed plan.

```powershell
.\Install-SgHy2Service.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash -ValidateOnly
.\Test-SgHy2Package.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash `
  -Python $Python -CloudflareCa $OfficialCloudflaredCa `
  -ExpectedCloudflareCaSha256 $ReviewedCaHash
```

The isolated test starts only its own headless processes, validates the SG exit,
checks all four GOST paths with TLS hostname/CA verification, then stops those
exact children and checks that its listeners disappeared. The Cloudflare Tunnel
edge certificate requires cloudflared's official CA and SNI `h2.cftunnel.com`;
do not replace this with disabled certificate verification. The test does not
create a Tunnel, call a paid model, alter production or prove stream stability.

## Install and roll production connectors

Run the reviewed installer from a normally elevated PowerShell. It does not invoke
UAC itself. A partial install is left for inspection and is not silently replaced.
If a temporary headless validation core is still using 17897, its owning operator
must compare PID, creation time and executable path against its private receipt
and stop only that exact process before installation. Port occupancy deliberately
fails closed; never kill a process just because it uses the candidate port.

```powershell
.\Install-SgHy2Service.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash
.\Switch-SgHy2Tunnel.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash -Role Replica -ValidateOnly
.\Switch-SgHy2Tunnel.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash -Role Replica
# Inspect replica receipt and complete stream/real-path acceptance before primary.
.\Switch-SgHy2Tunnel.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash -Role Primary
```

Each stage changes only that role's manifest edge list, WinSW dependency XML and
SCM dependency, then restarts only the selected connector. The other connector must
retain its original PID/start time and four old/new edge paths. The script checks
the selected cloudflared's four actual GOST TCP connections, controller node/traffic,
SG public exit, and complete public status bodies. It never force-restarts shared
`RealYuEdgeProxy`. These are network/status checks, not paid-model or long-SSE E2E.

Rolling restart preserves a serving peer but can end requests already attached to
the selected connector. Do not claim zero interruption for in-flight streams. The
existing WinSW graceful-stop deadline is 20 seconds; no mandatory 20-minute idle
wait is introduced. Failed or interrupted stages retain private backups and the
owned guard pause. Hash drift fails closed instead of overwriting another release.
A crash between a file replacement and its state receipt requires manual comparison
of the intent/state and private backups before continuing.

## Monitoring must follow the new path

The rolling script pauses only the guard's remediation, using its existing
`runtime\pause`; existing evidence sampling continues. It refuses another owner's
pause. Do not add a second permanent probe.

Preparation also stages exact, hash-pinned copies of the two existing collectors
and guard config in the private operation directory. After both Tunnel stages,
apply these without creating another probe:

```powershell
.\Set-SgHy2Monitoring.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash -ValidateOnly
.\Set-SgHy2Monitoring.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash
```

The monitoring script performs and verifies this bounded change:

1. Set `C:\ProgramData\RealYuTunnelGuard\config.json` `proxy` to
   `http://127.0.0.1:17897` and `proxy_config_path` to the installed `config.yaml`.
   The minimal YAML retains top-level `external-controller` and `secret` lines
   understood by the existing `path_observer.py`; no parser change is needed.
2. The live `C:\srv\realyu-observability\collect_evidence.py` has a hardcoded
   explicit proxy `7890` in `probe()`. Change its reviewed deployment source to
   `17897`, preserving
   10-second sampling and first-error semantics. Also update the maintained
   `C:\ProgramData\RealYuTunnelGuard\collect_evidence.py` copy to avoid later
   reinstall reverting this path. Do not report an old 7890 sample as SG coverage.
3. Restart only `RealYuEvidence` and `RealYuTunnelGuard`; verify all other RealYu
   service identities remain unchanged. Do not restart API/Tunnel services as a monitoring shortcut.
   Check fresh public-direct, public-via-local-proxy, backend, bridge, both Tunnel
   samples, and fresh path-observer flow IDs/start times with positive byte deltas.
   A bounded additional controller check requires both receive and send counters
   to advance on at least two stable SG flows for each of the four edge addresses.
   It stores the actual deltas in the private receipt, not merely nonzero lifetime
   counters. The six existing health paths remain the monitoring contract; any
   application migration must keep their semantics compatible separately.
4. After both roles are verified and fresh monitoring points at the right path,
   release the matching pause without a second connector restart:

   ```powershell
   .\Switch-SgHy2Tunnel.ps1 -Plan $NetworkPlan -ExpectedPlanSha256 $PlanHash `
     -Role Primary -Action Apply -ReleaseOnly
   ```

   `-ReleaseOnly` validates ownership, both stage states, manifest hash and guard
   paths; it does not restart a Tunnel. The operator must still verify current
   collector/observer samples. `-ReleaseOwnedGuardPause` can instead accompany the
   final planned stage when monitoring has already been updated.

Rollback uses `-Action Rollback -Role Primary`, then `-Role Replica`. It restores
the exact saved edge list and dependencies for each role while preserving the
peer. A failed new HY2 route is deliberately not a rollback prerequisite. Restore
the collector/guard proxy path using `Set-SgHy2Monitoring.ps1 -Action Rollback`
with the same plan/hash, then verify fresh old-path samples before releasing the
matching pause with `Switch-SgHy2Tunnel.ps1 -Action Rollback -Role Replica -ReleaseOnly`.
Monitor rollback accepts its own partially-applied files and preserves unrelated
drift instead of overwriting it. Keep the old GOST/Clash route and
private backups until the migration is accepted.

## Sub2API and boot acceptance remain separate

Sub2API OAuth/relay egress uses account `proxy_id`, not the Cloudflare Tunnel route.
Create a new native proxy record for `127.0.0.1:17897`, bind intended upstream
accounts in a controlled step, and verify fresh relay and OAuth-refresh traffic.
The scripts here do not modify Sub2API account bindings. Native pool invalidation
can be reused; avoid restarting the whole application just to change proxy URLs.

Before declaring unattended readiness: verify real SCM child processes in Session
0, service-account file access, automatic recovery, and startup without an
interactive login. Automatic service configuration alone is not a performed reboot
or logout test. Those checks and production rolling rollback have not been run by
this package's author. Short HTTPS/TLS success does not clear the first SSE outage
or establish a long-term guarantee.

References: [WinSW 2.12 XML and service behavior](https://github.com/winsw/winsw/blob/v2.12.0/doc/xmlConfigFile.md),
[Mihomo loopback/controller configuration](https://wiki.metacubex.one/config/general/).
