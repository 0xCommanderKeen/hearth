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

Sixteen fresh-SQLite checks cover pinned edits, other-resident/authority exclusion,
concurrency, digest and size limits, unsafe paths and sync failures before/after
publication. Fifty repeated concurrent staging checks pass after switching lock
creation to exclusive create followed by existing-file open on contention; the
original combined create/open intermittently failed with ENOENT on this Mac.
Full `make check` passes 289 backend and 35 browser tests plus both installed mock
journeys. Actual worker wiring and Mac filesystem/network isolation remain pending;
read-only file permissions are not evidence of confinement. No Codex call was made.

## Offline Mac container boundary

Issue #60 adds an opt-in credential-free probe using the local Docker Desktop
Linux VM on macOS 26.5.2 arm64, Docker 27.3.1 and a digest-pinned Python image.
[Recorded host evidence](evidence/mac-container-2026-09-06.json) verifies denied
unmounted canary reads, readonly input/root, bounded scratch, denied outbound
network, non-root/capability/no-new-privileges/seccomp controls and cgroup limits.
A live double-forked setsid descendant is terminated with its container; daemon
terminal state and unavailable process listing are checked before owned cleanup.

The daemon default is unconfined; explicit built-in seccomp and an in-container
Seccomp=2 assertion avoid relying on it. Dormant tunnel interfaces are present;
the probe checks no active non-loopback interfaces/routes and connection denial.
Three offline cleanup checks cover daemon loss and foreign-ownership refusal.
Full `make check` passes 292 backend/35 browser plus both installed mock journeys.
This is host evidence for the synthetic image, not a Codex integration or a real
summary. [Reproduction and remaining gates](mac-isolation.md) cover actual staged
mount permissions, durable worker identity, model transport/credential separation,
pricing, real-test selection and the remaining observation/operation gates.

## Pinned-input container ownership rehearsal

Issue #62 connects actual `stage_run` 0400/0700 files to a non-root network-disabled
container without broadening permissions. A durable claim precedes create and the
exact inspected ID is persisted before start. Replay only inspects; missing, changed
or uncertain evidence never authorizes another create/start. Ownership guards stop
and removal. Terminal logs use the offline Codex parser with run-ID matching and
observed container exit; no token/dollar usage is invented.

The actual Mac rehearsal passed success and held-cancellation cases, including a
lost start acknowledgement and observation from a fresh trusted process. It uses
fresh SQLite, synthetic memory and the fixed Python image; no Codex or credentials.
[Evidence and remaining integration](container-rehearsal.md) distinguish this from
an application runtime and describe unavailable terminal logs after explicit cleanup.
Full `make check` passes 306 backend/35 browser plus both installed mock journeys;
14 fault-boundary tests use real SQLite, and the Mac rehearsal is separately opt-in.

## Durable container terminal evidence

Issue #64 preserves bounded immutable terminal receipts tied to the exact claim
and container ID before cleanup. Validated raw events and exit status survive
container removal and daemon loss. Corrupt/missing receipts remain unknown; competing
conflicting captures leave a durable hold marker. No evidence is overwritten and
storage failures prevent removal. Invalid transcripts remain visibly invalid, with
no invented usage or costs. Automatic container restart is explicitly disabled.

Full `make check` passes 330 backend/35 browser and both installed mock journeys,
including 38 container ownership/receipt checks. Twenty concurrent-conflict
repetitions pass. The updated actual Mac rehearsal verifies success and cancellation
receipts survive container removal; its report records the final module hash.
[Receipt behavior and remaining gates](container-rehearsal.md) still defer operational
worker/backup integration, actual Codex/model-channel execution and real acceptance.
