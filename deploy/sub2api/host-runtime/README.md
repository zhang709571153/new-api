# Native API and transparent edge runtime sources

These sources complete the Python runtime dependencies of the native Windows
launcher. They contain no credentials, database or private service manifest.
Copy according to `source-manifest.json`, and verify each SHA256 first.

| Source, relative to `deploy/sub2api` | Installed destination |
| --- | --- |
| `service_entry_singlecore.py` | `<service-base>/service_entry.py` |
| `host-runtime/job_guard.py` | `<service-base>/job_guard.py` |
| `host-runtime/service_identity.py` | `<service-base>/service_identity.py` |
| `edge_proxy.py` | `<edge-root>/lab/image_bridge.py` |
| `bridge_observability.py` | `<edge-root>/lab/bridge_observability.py` |
| `release_control.py` | `<edge-root>/lab/release_control.py` |

The bridge file is named `edge_proxy.py` in Git and `image_bridge.py` on the
current host; their bytes are identical. Its historical New API description
is retained. The bridge forwards HTTP/SSE/WebSocket to port 18300; the currently
selected API core owns authentication, accounting and model behavior.

The native API and bridge launcher branches do not open old New API credentials,
SQLite or executable files. The `NEWAPI_PORT` variable in the bridge branch is
the existing transport listener contract, not a second application core. The
legacy API branch is retained solely for recovery before opening the new core.

Install a persistent Python 3.11+ Windows runtime at `<service-base>/python`;
these modules use only the standard library. Use the reviewed WinSW services,
private `manifest.json`, service XML, paths and ACLs described in
[the Windows handoff](../SINGLECORE-WINDOWS-HANDOFF.md). The manifest must include
`singlecore_api`; never omit it to make a validation failure fall through to
the legacy application. A missing or invalid native entry is not a supported
recovery after customer traffic has been admitted.

Create `<edge-root>/.lab` only for admission state, bridge PID and observation
logs; no customer database or retired backend credentials are needed there.
The bridge WinSW XML must supply `REALYU_UPSTREAM_DRIVER=sub2api`, as the current
installed XML does; the transparent edge refuses other or missing driver values.
Create the service log directories before starting the wrappers. The tunnel
still needs its separate private `lab/cloudflared.yml` and tunnel credentials;
observation and SG service sources are delivered separately. This directory
does not install or configure third-party binaries or replace a reviewed host
plan. Machine reboot/no-login acceptance must be performed on the target host.
