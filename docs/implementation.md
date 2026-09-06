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

The fresh-start baseline passed 204 backend and 35 browser tests, lint, types, builds and
wheel checks. Fresh CLI summary/restart and current-schema backup/restore preserve
memory and audit with the copied store held and the backup unchanged. The release
wheel contains the direct schema and none of the removed modules. Rendered desktop
and mobile journeys verify setup, memory save/conflict/reload and a pinned summary
result. Independent Standards and Spec reviews found no blocking issue; they also
ran 77 and 108 focused checks respectively.

## Standalone release verification

Issue #46 adds an isolated installation check to `make check`. It exports hashed
runtime dependencies from the lockfile, installs the wheel without the editable
checkout, and runs from an unrelated temporary directory with Python isolated mode.
The installed CLI passes twice against the same fresh data. Loopback HTTP verifies
packaged assets, authentication, Reader setup, duplicate command receipts, a
simulated result, restart persistence and a held current-data restore. Temporary
files and servers are cleaned up. See [the installation recipe](release.md).

Miha selected Codex Astra, synthetic notes, this Mac for development and $10 per
day. Fresh CLI/browser Reader setup uses 10,000,000 microdollars and Europe/Ljubljana
budget days; repeated setup preserves existing declarations. Exact runtime,
authentication and pricing remain open. Real execution is still deferred.

## Selected runtime design

Issue #48 records the [Codex Astra integration design](codex-reader.md), grounded in
current official documentation, local CLI 0.145.0 help and the current Hearth code.
The CLI has a JSONL interface, but launch idempotency, process ownership, complete
host isolation and a hard dollar cap are not established by those flags. The design
orders explicit provenance, a durable fake-process worker, bounded event parsing,
staged inputs and actual-host acceptance before real wiring. Model access, billing and
Mac process/isolation behavior remain unverified. No Codex task was launched.

## Process lifecycle rehearsal

Issue #52 implements a process mock through the existing Runtime
contract. [The rehearsal](process-mock.md) uses a detached trusted worker, durable
launch/started claims, bounded synthetic child output and durable cancellation.
Real processes and temporary SQLite verify restart recovery and retention of
ownership after uncertain launch. Its baseline passed 216 backend and 35 browser
tests; both independent reviewers also ran all 12 process checks.

Issue #54 connects the process mock to fresh application stores. Store runtime
choice is immutable; runs pin adapter kind/version and serialized input digest at
admission. Wrong adapters or mismatched evidence cannot execute or settle a run.
Authenticated snapshots and run inspection expose the pins. Quiescent process
backups preserve validated durable claims; active/unknown runs refuse capture.
Restored stores infer their runtime and stay held. The schema is defined directly;
incompatible prototype layouts require fresh data and are never converted.

Real model/pricing provenance, live Codex event compatibility and Mac confinement remain
pending. This wiring enables process-backed simulations only. Full `make check`
passes 226 backend and 35 browser tests. Installed-wheel HTTP journeys pass for
both runtimes, including pinned result/restart and verified held backup restore.

## Codex event interpretation

Issue #56 adds an [offline exec JSONL parser](codex-events.md), tested only with
synthetic streams. It bounds framing/output, validates lifecycle and token values,
preserves unknown usage and ambiguous final messages, and requires observed process
exit before completed interpretation. It returns no runtime evidence or dollar cost.
Real CLI event compatibility, final-file ownership, pricing and Mac isolation remain
pending; no Codex task was launched. Full `make check` passes 273 backend and
35 browser tests, including 47 synthetic parser checks and installed-wheel journeys
for both mock runtimes.

## Remaining acceptance

| Gate | Current state | Required next evidence |
| --- | --- | --- |
| Fresh mock workflow | Full checks, fresh CLI/browser/backup rehearsal and both reviews pass | Reassess simplicity against the selected real task before expanding scope |
| Bounded real Reader | Deferred by mock-only direction | Mac isolation, exact runtime/authentication/pricing; useful output and actual read-only isolation |
| Real recovery and daily use | Not yet demonstrated | Real cancellation/usage/restart/notification/restore, seven days and ten representative tasks, native accessibility |
| Long-term operation | Not yet deployed | Reproducible deployment, release identity, retention and operator recovery on the intended host |

The mock runtime emits a fixed fixture. Its output does not establish real summary
quality or model behavior. No migration, source mapping, ownership transfer or
retirement work remains in the project scope.

## Pinned worker input staging

Issue #58 adds the internal `stage_run` helper: one SQLite snapshot, exact admitted
context digest, bounded canonical JSON and private atomic publication. Retry checks
preserve exact bytes and refuse partial, changed or linked destinations. The only
published input is `context.json`; no worker activation or launch authority is added.
See [the boundary and remaining work](staged-input.md).

Fourteen fresh-SQLite checks cover pinned edits, other-resident/authority exclusion,
concurrency, digest and size limits, unsafe paths and sync failures before/after
publication. Fifty repeated concurrent staging checks pass after switching lock
creation to exclusive create followed by existing-file open on contention; the
original combined create/open intermittently failed with ENOENT on this Mac.
Full `make check` passes 287 backend and 35 browser tests plus both installed mock
journeys. Actual worker wiring and Mac filesystem/network isolation remain pending;
read-only file permissions are not evidence of confinement. No Codex call was made.
