# Existing-host service tools

These reviewed sources come from the preparation of the 2026-10-09 local host
profile. They contain no credentials, private receipts, databases, or binaries.
They are not a generic installer for another Windows machine. For another host,
start with [NATIVE-WINDOWS.md](../NATIVE-WINDOWS.md), review its private paths,
service names, ports, account permissions and artifact hashes, and complete its
isolated acceptance before any traffic migration.

All scripts require explicit `-DeploymentRoot` and `-ReleaseRoot`. This profile
retains fixed ports 28090/28490/28391, service IDs ending in `20261009`, and pinned
binary hashes. Do not edit a pin merely to make an unreviewed executable pass.

| Script | Action and boundary |
| --- | --- |
| `Capture-TemporaryProcesses.ps1` | Validates the exact three temporary process paths and listeners, then writes a private ownership receipt. Rejects already installed services. |
| `Stop-TemporaryProcesses.ps1` | Validates receipt schema, names, paths, ports, PID and creation time. Without `-StopValidatedProcesses` it stops nothing. Explicit stop terminates only the recorded Sub2API process and gracefully stops this profile's Redis/PostgreSQL. Refuses service-owned processes. |
| `Install-Sub2ApiServices.ps1` | `-ValidateOnly` checks the prepared private XML and binary pins without mutation. The explicit elevated run installs the three new services and optionally the worker, configures scoped SID ACLs, and starts them. It refuses existing service names. It does not modify old RealYu/tunnel services. |
| `Verify-Services.ps1` | Read-only SCM, listener/executable ownership and health checks; optional worker metadata checks. No model request, financial write, process stop or configuration change. |

Example for this host profile, after its private configuration is prepared:

```powershell
$deploymentRoot = 'C:\ProgramData\RealYu\sub2api-production-20261009'
$releaseRoot = 'C:\RealYu\releases\sub2api-0.2.15'
powershell -NoProfile -File deploy/sub2api/host/Install-Sub2ApiServices.ps1 -DeploymentRoot $deploymentRoot -ReleaseRoot $releaseRoot -IncludeWorker -ValidateOnly
powershell -NoProfile -File deploy/sub2api/host/Verify-Services.ps1 -DeploymentRoot $deploymentRoot -ReleaseRoot $releaseRoot -IncludeWorker
```

Successful validation does not itself authorize the mutating installation or
temporary-process stop. Include those explicit commands only in the reviewed
release operation. Service installation is not an idempotent repair command: a
partial installation is retained for inspection rather than blindly deleted or
reinstalled. Existing RealYu service SID enablement remains the main cutover
operator's responsibility. `LocalService` plus per-service SIDs is not equivalent
to separate Windows user accounts.

After a completed gateway rollback with these dependencies still running, a
fresh pinned host plan can select `dependency_mode: "reuse"`. The elevated
`Install-HostRelease.ps1` then runs the pinned read-only `Verify-Services.ps1`
(including executable identity and application hashes) before cutover. It does
not reinstall the four services. Use a new operation directory and verdict;
`production_route.py restage` verifies the pinned prior `restored` receipt and
current disabled route/legacy channel baseline without changing routing.
Do not rerun an already completed or rolled-back operation directory.

The one-off private `prepare-production.py`, `enable-production-images.py` and
`verify-production-runtime.py` are deliberately not included. They contained
historical workstation assumptions and mutating initialization/acceptance steps.
The fresh-machine procedure uses the maintained candidate generator and normal
Sub2API management interfaces. In particular, operator confirmation, management
key creation and group image enablement must remain explicit, reviewable steps.
The eight historical persistence checks are evidence of that specific fixture,
not a reusable license to restart a serving installation.

Private inputs that must travel separately under access control include service
XML, runtime credentials, bindings with immutable namespace/identity secret,
queue/state/credit audit, the current PostgreSQL dump and Redis persistence data.
No files in those categories belong in this directory or Git. The read-only
verification result does not certify OAuth refresh, model availability, prices,
public ingress, service restart resilience or machine reboot recovery; perform
the corresponding acceptance tests separately.
