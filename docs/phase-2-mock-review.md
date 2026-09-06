# Mock core review before import work

Date: 2026-09-06. This is a review of the implemented mock core, not completion of
the plan's Phase 2 exit gate. The useful real workflow and actual-host authority
checks remain unproven under the current mock-only direction.

## What became simpler

There is one SQLite authority for declarations, commands, tasks, runs, grants,
approvals, action intent, routine occurrences, delivery intent, and audit. Ordinary
state changes pair their audit facts in the same transaction. The browser reads
one snapshot/stream and receives ordinary command receipts; it does not wait for a
second telemetry authority to acknowledge its commands.

Manual and routine tasks share task creation and admission. Runtime evidence,
artifact files, and effect receipts remain separate because they describe facts
outside the database transaction. They cannot independently approve or reassign
owned work. Unknown evidence retains ownership rather than becoming a retry rule.
There is one backend/browser release artifact and no separate configuration watcher,
remote scheduler, delegation framework, plugin system or event replay database.

The important module interfaces own their failure handling: Execution owns launch/
cancel/finish evidence; Broker owns exact permission consumption and uncertain
effects; Routines owns occurrence identity; Notifications owns delivery retries;
Backup owns a consistent copy and held restore. These modules earn their separation
by keeping ordering and recovery out of the browser and other callers. There is no
reason yet to split them into separately deployed processes or generic frameworks.

## What remains incomplete

- A short-lived exact-run read-only context credential is now tested through mock
  HTTP clients (issue #19; `run-context-access.md`). Actual runtime injection and
  host isolation remain pending; current mock approval requests are operator-driven.
  A real resident must never receive the operator token, database, engine socket
  or effect credentials.
- Process-lifetime supervisor ownership and drained shutdown are now implemented
  and tested (issue #17; `supervision.md`). Standalone mock operations retain their
  per-operation locks; this is not a v1/v2 transfer guard.
- Unknown usage now has an explicit immutable operator-reported mock reconciliation
  flow (issue #21; `mock-usage-reconciliation.md`). It preserves unrelated holds.
  Real provider evidence ingestion and corrections remain pending.
- Restore is deliberately read-only. There is no activation/ownership transfer,
  reverse export, compatible cross-version restore rehearsal, or live memory copy.
  Backups currently require the current schema; the package version alone is not a
  sufficient production release identity. Runs still need exact runtime/pricing/
  policy execution provenance when a real adapter is selected.
- Native browser visual/accessibility verification, actual-host isolation, real
  cancellation/usage/tool enforcement and useful output checks remain pending.
- PRs are stacked and unmerged; passing their checks does not establish a shipped
  release. Review/merge and production deployment verification are separate work.

## Decision within current authorization

Continue bounded mock implementation of the missing authority/supervision contracts
and synthetic import/transfer rehearsals. Do not expand into live residents or
claim the Phase 2 exit gate. Reassess this decision against the actual selected
runtime and a useful task before live expansion; if enforcement requires bringing
back competing authorities, stop and redesign that seam.

The review exposed a concrete supervision omission, now addressed: revisioned
operator pause/resume gates new admissions. Safety holds remain independent reasons
for refusal in the same database. Existing work retains its actual runtime state
and explicit cancellation; pausing never claims it has stopped.

Verification at this checkpoint: `make check` passes with 116 backend tests and
24 browser tests, including pause/resume, conflicting controls, unknown-usage hold
preservation, cancellation, and audit rollback. The behavioral matrix now names
mock evidence and remaining real gates explicitly.
