# Pinned Reader input staging

`hearth.execution.staging.stage_run(database, run_id, root)` is an internal building
block for the future trusted worker. It reads the run's declaration, task, memory
revision and synthetic notes in one SQLite snapshot, serializes the existing
context contract, and checks the admission-time SHA-256 before any publication.
The context is capped at 512 KiB. This operation does not authorize a launch.

A published run directory contains only `context.json`, with exact canonical JSON
bytes matching the run digest. It contains no database, owner token, operator
credential or other resident's memory. Purpose, skill, task, memory and notes stay
separate JSON fields; source text cannot grant additional permissions.

The caller prepares the root's parent. Staging creates only the root itself and
syncs its parent entry before publication. The root and run directories are private (0700); the file is read-only
(0400). A no-follow, regular, singly linked lock serializes publication. Lock
creation is exclusive, with an existing-file open on contention. Data and the
private temporary directory are synced before rename, then the root is synced.
Retries compare the complete file and refuse changed bytes, extra/missing files,
symlinks and hard-linked input files. They never repair or overwrite a conflicting
run directory. Temporary directories left by a killed worker are not launchable
inputs and may be removed by the trusted operator while no staging worker runs.

The caller must supply a dedicated trusted root with trusted ancestors, outside
untrusted writers. Same-user code can change permissions: these modes are **not a
sandbox**. The future worker must mount or expose only the selected run directory,
keep its control/evidence files outside that boundary, and recheck current launch
authority. A declaration edit can invalidate launch even though the old context
remains available for exact inspection. Returning a path does not prevent later
host tampering; isolation and launch-time verification remain required.

This helper is deliberately not wired into the mock executors or a real runtime
selector. It is exercised against fresh SQLite and synthetic notes. Mac filesystem
and network confinement, credential separation, descendant termination, actual
Codex compatibility and paid execution remain unverified and disabled.
