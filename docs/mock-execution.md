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

The executor sends canonical JSON context version 1: run/task/resident identities,
the declaration revision pinned at admission, its purpose, the task instruction and
the fixed synthetic notes. The scoped runtime context route uses the same reader.
Ownership tokens, credentials and other residents' data are absent. MockRuntime
persists only the input digest, not the purpose or task text; its summary remains
a deterministic fixture and does not interpret these instructions.

Launch authorization rechecks the current declaration revision. If it changed
before authorization, no new runtime starts and work becomes interrupted. Cancel
and submit a new task to use the new declaration. Cancellation can settle a run
without launch intent at zero usage; an existing launch intent retains uncertainty
until evidence resolves it. A change after authorization cannot retract input
already authorized: the runtime receives the original pinned revision. Existing
runtime evidence is reconciled even when the current declaration has changed.
Restart with the same context version reconstructs identical input from immutable
task/declaration records. Changes to the synthetic fixture or context format need
an explicit compatibility decision; this is not real runtime provenance.

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

Known usage settles the reservation and counts in resident-local budget windows
by the run's admission timestamp; see `local-budget-windows.md`. Outstanding runs carry exposure across midnight and block overlapping
admission. Unknown usage creates a persistent resident pause; it never counts as
zero or silently resets at midnight. A reservation is a scheduling/accounting
policy, not a guarantee that a provider would stop at that amount.

Schema 2 rebuilds the foundation's constrained task/run tables transactionally.
The upgrader checks foreign keys before committing; failed migration restores
schema 1 and its records. A schema-1 binary refuses schema 2 rather than running
with an incompatible lifecycle. No automatic downgrade is supplied.

Malformed terminal cost/output is unknown evidence. It retains the run's ownership
and does not prevent unrelated residents from making progress in the same pass.

Browser interfaces, explicit usage reconciliation, operator pause, approvals,
routines, notifications and held backup/import rehearsals are implemented in mocks.
Bounded observation retention, actual-host isolation, compatibility conversion and
live acceptance remain separate gates; see `implementation.md`.
