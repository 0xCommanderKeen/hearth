# Implementation checkpoint

Hearth is a standalone project with a new read-only daily-summary Reader. Miha
removed all migration requirements on 2026-09-06. The active scope is in
[the project plan](rebuild-plan.md); current work remains mock-only.

## Fresh-start simplification

Issue #44 removes portable state import/export/diff/upgrade, historical schema and
backup upgrades, the cross-system ownership registry and its rehearsal, migration
inventory, and associated tests/docs. Issue #43's source exporter was cancelled
before implementation. The schema is defined directly for fresh initialization;
only the current layout is accepted. Existing incompatible data is never rewritten.

Normal operational ownership remains: run tokens, active-run exclusion, supervisor
lifetime, cancellation and uncertain execution are required even in a new project.
Revisioned declarations/skills/memory, current-data backups and held restore remain.
No existing data directory has been deleted, migrated or activated.

## Verified mock behavior

The application has one SQLite authority and one backend/browser artifact. Manual
and daily tasks share durable commands and admission. Persistent mock runtime
receipts support restart and cancellation; unknown usage holds further admission.
Exact operator approvals govern local mock publication, with checksummed receipt
recovery. Notifications retain stable identity and retries. Hamlet and Townhall
share authenticated snapshots/SSE and preserve editing drafts across conflicts.
Memory is immutable, checksummed and pinned at admission; skills belong to the
pinned declaration revision. Reader can only read its context.

Full `make check` passes: 204 backend and 35 browser tests, lint, types, builds and
wheel checks. Fresh CLI summary/restart and current-schema backup/restore preserve
memory and audit with the copied store held and the backup unchanged. The release
wheel contains the direct schema and none of the removed modules. Rendered desktop
and mobile journeys verify setup, memory save/conflict/reload and a pinned summary
result. Independent review verification is in progress.

## Remaining acceptance

| Gate | Current state | Required next evidence |
| --- | --- | --- |
| Fresh mock workflow | Full checks and fresh CLI/browser/backup rehearsal pass | Independent review of the simplification |
| Bounded real Reader | Deferred by mock-only direction | Source/runtime/model/host/allowance decisions; useful output and actual read-only isolation |
| Real recovery and daily use | Not yet demonstrated | Real cancellation/usage/restart/notification/restore, seven days and ten representative tasks, native accessibility |
| Long-term operation | Not yet deployed | Reproducible deployment, release identity, retention and operator recovery on the intended host |

The mock runtime emits a fixed fixture. Its output does not establish real summary
quality or model behavior. No migration, source mapping, ownership transfer or
retirement work remains in the project scope.
