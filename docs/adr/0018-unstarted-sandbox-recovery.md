# ADR 0018: Recover an unstarted management run after verified daemon absence

Status: accepted, 2026-09-10.

## Context

A Docker bind failure can end management discovery before a model turn, without
writing a container ID. Cancellation records intent but cannot remove an unnamed
container. The run then blocks its resident indefinitely, even when the daemon
holds no agent containers.

## Decision

For a management receipt validated against its request binding and recording no
model-turn dispatch, a missing container identity may be reconciled automatically
when the worker lock is free and the run's configured daemon successfully reports
an empty inventory of all containers bearing Hearth's sandbox label. The inventory
includes created, stopped, and running containers. Errors, timeouts, unexpected
output, or any remaining container keep the run held. No container is removed by
this fallback, and other residents' sessions are never touched.

The existing worker publishes its terminal receipt only after its sequential
sessions have ended; it performs no later launches. Combined with its released
lock and the daemon's fresh absence observation, this provides termination evidence.
The saved receipt still determines the result and accounting. A late cancellation
request does not rewrite an earlier startup failure as cancelled. Settlement checks
the receipt against the actual database run and pins, and records task state, usage,
and terminal audit facts in the existing transaction. The original receipt remains
unchanged. Container absence is separately audited as a measured observation.

## Consequences

This fixes no-turn startup failures without an operator override or database edits.
It deliberately does not recover lost identities after model dispatch, missing
receipts, or unknown usage. An unrelated Hearth container may delay this narrow
fallback until the daemon is empty; ordinary identified-container cleanup remains
per-run. Existing restore restrictions and one-active-run admission still apply.
