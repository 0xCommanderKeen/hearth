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
budget days; repeated setup preserves existing declarations. ChatGPT subscription authentication is selected; API keys and API billing are
excluded. Exact runtime, credential isolation and subscription usage remain open. Real execution is still deferred.

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

## Operational container dispatch prerequisite

Issue #66 remains in progress. `Execution.dispatch_guard` now serializes the final
container start against SQLite cancellation and declaration changes, checking owner,
epoch, pinned input, runtime contract and launch intent. A held restore refuses.
Refused or uncertain starts retain their inspect-only container claims. The Mac
rehearsal now supplies this guard using a fresh process-mock database.

Validation: 343 backend and 35 browser tests, lint/types/build and both installed
mock journeys passed. Thirteen additional checks cover active observations, changes
during container creation, concurrent writer exclusion and lost start replies.
The actual Mac synthetic success/cancellation rehearsal passed with guarded dispatch
and terminal receipts surviving cleanup. This does not enable a container selector
or complete issue #66: detached worker wiring, reconciliation and quiescent backup/
held restore for container evidence remain next. Real Codex execution stays deferred.

## Operational contained mock and held recovery

Issue #66 connects the guarded container fixture to the existing process runtime and
normal executor. Fresh stores pin the process boundary; reopening infers it and a
conflicting selector refuses. Private requests pin owner/epoch/input; only synthetic
context reaches the container. The detached worker observes cancellation and timeout,
and the reopened adapter can reconcile and stop an owned container after worker loss.
Synthetic receipts become ordinary simulated artifacts and accounting. Unknown
execution still prevents retry or release of the active run.

Quiescent backup preserves and validates container claims, identities, staged input
and terminal receipts against the process request, run and artifact. Verification
uses no Docker access. Held restores cannot execute, and repeated held generations
retain original execution provenance. Current required configuration/request fields
are defined directly; incompatible prototype data is refused without conversion.

Validation: full `make check` passed 362 backend and 35 browser tests, lint/types/build
and both installed inline/POSIX mock journeys. Nineteen new integration checks cover
normal execution, restart, worker loss, cancellation before/during dispatch, timeout,
input contradiction, active-worker capture refusal, receipt corruption and repeated
held recovery. Review regressions cover persisted timeouts after worker loss and
receipt conflicts during cleanup/cached-result observation. The actual Mac worker
rehearsal verifies synthetic success, cancellation,
app restart, loss of the trusted worker and its retained timeout, plus container
cleanup and held restore.
See [configuration and evidence](container-worker.md). This completes the contained
mock integration, not actual Codex/model-channel execution or daily-use acceptance.

## Offline subscription CLI compatibility

Issue #69 now includes a reproducible, opt-in CLI 0.145.0 probe with synthetic
ChatGPT authentication and a local WebSocket model fixture inside the network-none
Mac container. It verifies the selected model string, final-file/JSONL agreement,
synthetic authentication headers and rejection of injected shell/image calls for
this text-only profile. The fifth reported token counter, cache-write-input, is
preserved separately by the parser without assigning any dollar amount.

See [probe reproduction and limits](codex-subscription-probe.md). This does not
complete issue #69 or enable the Codex runtime: production credential separation,
transport failure/recovery and operational integration remain to be established.
No real account, login, model call, subscription charge or source data was used.
Full `make check` passed 372 backend and 35 browser tests, lint/types/build and both
installed-wheel journeys. The [Mac report](evidence/codex-subscription-2026-09-06.json)
records successful completion and tool-injection cases with exact source pins.

Miha subsequently confirmed a shared accounting policy: price subscription tokens
at API-equivalent rates so a future API switch retains the same usage calculation
and $10/day budget. Implementation of the versioned estimator remains next;
subscription estimates must remain separate from actual provider charges.

Four probe recovery checks cover lost/invalid create replies, foreign ownership
and unavailable daemon observation; uncertain creation never authorizes relaunch.

## Shared API-equivalent cost calculation

Issue #71 adds the [pinned Astra estimator](codex-pricing.md), independent of
subscription/API authentication. It prices request-level ordinary/cache input and
output with long-context/Fast multipliers and integer rounding. Missing or invalid
usage remains unknown; a turn aggregate cannot establish per-request prices.
The actual offline CLI probe now verifies nonzero cache/read/write and reasoning
counter mapping and both synthetic cost examples. Operational accounting still
requires complete request provenance and admission-time schedule binding.

Validation: full `make check` passed 404 backend and 35 browser tests, lint/types/
build and both installed-wheel journeys. Thirty-two pricing checks cover token
categories, long-context thresholds, Fast rates, rounding and unknown usage.

## Durable request usage evidence

Issue #73 adds the [run-bound usage journal](codex-usage.md). Intent precedes
transport dispatch; completion is immutable and conflicting receipts remain held.
Unresolved or unknown previous usage refuses another intent. Terminal CLI/final
output and known counter totals must agree; reopened and copied receipts recompute
costs without model execution. This prepares settlement but does not mutate budgets.

The offline CLI fixture now persists the journal outside container scratch and
checks it after container exit. Production collector isolation, admission pins,
atomic accounting/audit and backup integration remain next; no real credentials
or provider calls are enabled.

Validation: 432 backend and 35 browser tests, lint/types/build and both installed
journeys passed. Twenty-eight real-file journal checks cover interruption, conflict,
reopening, mismatched bindings, malformed/linked evidence, sync failure and
concurrent intent. The actual Mac probe passed completion, tool injection and
interrupted-request persistence with owned container cleanup.

Review regressions verify type-sensitive duplicate receipts, sticky late conflicts
after sealing and successful file/directory resynchronization before trusting
recovered evidence left by failed writes.

## Atomic API-equivalent settlement

Issue #75 binds model, pricing mode and schedule at synthetic run admission, then
settles a matching sealed journal into the existing budget. Receipt, calculated
cost, artifact reference and audit commit together. Missing usage retains the
admission hold; scalar or contradictory evidence cannot settle a priced run.
Current-data backup/held restore independently revalidates the SQLite receipt and
preserves explicit operator reconciliation. Run inspection and the browser identify
API-equivalent simulated amounts. See [the accounting seam](codex-accounting.md).

Full `make check` passed 448 backend and 35 browser tests, lint/types/build and both
installed-wheel journeys. Sixteen accounting tests cover ownership/binding,
standard/Fast pricing, $10/day exposure, atomic rollback, unknown usage and held
recovery. An unsupported priced run stays interrupted while unrelated mock work
continues. This does not enable a Codex worker or complete issue #69; production
collector isolation, terminal handoff and operational dispatch/recovery remain.

Review regressions ensure missing counters cannot mask contradictions in known
request/CLI totals or cache subsets. The refreshed Mac probe passes all three
synthetic scenarios with the final journal and pricing source pins.
