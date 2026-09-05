# Mock execution contract

Miha requested mock-only development. The shipped adapter calls no model, reads no
live notes, and performs no external action. Its fixed summary is a fixture for
testing the lifecycle, not evidence of reasoning or real task completion. All
artifacts and synthetic usage are labeled simulated.

`Executor.step()` takes a single nonblocking lock derived from the canonical
database path, inspects active runs, and reconciles them. `MockRuntime` persists
evidence under stable run IDs so a new application process can recover an old
result. Repeating start with the same identity/instruction is idempotent; changed
instructions under that identity are refused.

Launch intent is durable before calling start. Cancellation records desired state;
confirmed runtime cancellation records the terminal. Never-launched work can be
cancelled without starting the adapter. Missing evidence after launch is unknown
and holds resident ownership. The adapter's private evidence files are not a second
operational database; they represent the simulated external runtime being observed.
No production runtime can be enabled through the demo CLI.

Successful evidence must contain nonempty bounded output. Hearth publishes an
immutable checksummed artifact, then records its reference with the terminal state,
synthetic usage, and audit fact in one database transaction. Publication followed
by a failed commit leaves an orphan; replay verifies and reuses identical bytes.
An orphan is not visible as an artifact until the database reference commits.

Known usage settles the reservation and counts against the run's admission-day UTC
allowance. Outstanding runs carry exposure across midnight and block overlapping
admission. Unknown usage creates a persistent resident pause; it never counts as
zero or silently resets at midnight. A reservation is a scheduling/accounting
policy, not a guarantee that a provider would stop at that amount.

Schema 2 rebuilds the foundation's constrained task/run tables transactionally.
The upgrader checks foreign keys before committing; failed migration restores
schema 1 and its records. A schema-1 binary refuses schema 2 rather than running
with an incompatible lifecycle. No automatic downgrade is supplied.

Pending: authenticated browser interfaces, pause resolution, approval broker,
schedules, bounded observation retention, backup/restore, and import rehearsals.
Real-host isolation and live external action semantics remain separate future gates.
