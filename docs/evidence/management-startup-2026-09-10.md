# Docker management startup and resident activity

Validated on the local Docker Compose installation on 2026-09-10.

## Failure

A management run became interrupted with `sandbox_termination_unknown`, no
recorded model turn, no container identity and no management calls. The backend
created its generated Codex catalog under its private `/tmp`, then asked the host
Docker daemon to bind that same path. A no-model bind probe reproduced Docker
exit 125 because the source did not exist on the host.

After moving the catalog beside the workspace on the shared run volume, a second
problem became observable: the daemon's explicit missing-container response was
`Error response from daemon: No such container: <id>`. The launcher recognized
only `Error: No such container: <id>` and treated successful removal as unknown.
Both exact responses are now recognized; other identities and connection errors
remain unknown.

## Verification

- A real sandbox startup probe initialized both management sessions and removed
  them successfully. Its thread callback deliberately stopped execution before
  any model turn was submitted.
- The rebuilt application was deployed locally by image digest. The authenticated
  resident activity endpoint returned HTTP 200 with `Cache-Control: no-store`,
  the resident's timeline, and a fixed explanation from the bound receipt.
- `make check` passed: 1,170 backend tests, 197 frontend tests, formatting, types,
  frontend build, and installed-wheel smoke checks.

The original interrupted run was preserved with its reservation held. Missing
historical container identity is not evidence of termination, and the task was
not replayed. This validates startup and cleanup, not a completed model task or
the daily-observation acceptance gate.

## Activity visibility

Each resident page now displays its audit history, with timestamps, run links,
automatic refresh as events arrive, and pagination beyond the recent snapshot
window. Failure explanations expose only allowlisted messages from receipts
validated against the run's binding. The endpoint excludes raw runtime output,
task instructions, credentials, and audit detail payloads.
