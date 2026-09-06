# A shared execution fence for mock handoff rehearsals

The rebuild plan requires a durable ownership guard understood by both launch
paths. Two independent control-plane databases cannot enforce that exclusion using
only their own active-run indexes. A new repository or a stopped supervisor is not
proof that an old owner cannot restart.

For the synthetic handoff rehearsal, use one separate protected SQLite registry
outside both work-data roots. It owns only system-to-database bindings, resident
execution ownership, outstanding run claims, stable transfer receipts and their
audit facts. It stores no task instructions, budgets, memory or operational status.
Each work database remains its own authority; registry history is not replayed to
rebuild either work database. This is the narrow shared authority required for
the handoff, not a second operational event log.

A participating executor claims a resident before runtime inspection/control and
keeps the claim across crashes. It releases only after its bound source database
records terminal work and known accounting. Transfer and claim use the same SQLite
write serialization, so either the claim blocks transfer or transfer rejects the
old owner's claim. There is no lease expiration or timeout that releases authority.

This proof is scoped to two fresh mock control planes. Ordinary API construction
does not enable this guard, no existing runtime is adopted, and no live launch path
is modified. Production adoption requires both real launch paths to enforce the
same protected registry, plus source inputs/effects stopped, data and budgets
reconciled, and actual host validation. Copying or recreating a registry cannot be
used as a recovery shortcut. A missing registry fails closed.
