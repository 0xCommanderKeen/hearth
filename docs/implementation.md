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

The mock runtime deterministically formats supplied synthetic notes. Its output does not establish real summary
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


## Separate offline CLI and collector

Issue #77 removes the collector journal from the actual Codex CLI container. The
network-none collector and CLI share loopback only, with separate filesystem/PID
namespaces. Only the collector receives the journal and a synthetic secret canary;
direct CLI-container reads/writes to those paths fail. The host requires stopped
collector handlers and a durable report before sealed usage handoff. Independent
owned claims allow cleanup of the collector even after uncertain CLI creation.

The actual Mac probe passed completion, tool injection and interrupted-request
preservation with this split boundary. This remains an offline synthetic fixture,
not real credential forwarding or the operational Codex adapter. See
[reproduction and limits](codex-subscription-probe.md).

Validation: full `make check` passed 450 backend and 35 browser tests, lint/types/
build and both installed-wheel journeys. Six offline ownership checks cover lost
create replies, foreign identities, daemon loss and cleanup of the other owned
container when the second creation fails.

## UI alignment checkpoint — 2026-09-06 (working branch)

User prioritized visible Warren parity over further runtime expansion. Townhall
now has dark sidebar navigation, an all-residents directory and hash-addressable
resident profiles with purpose, status, limit/timezone, memory/skills and work.
Hamlet uses Warren's original Three.js miniature art kit (from the issue-466
worktree), with orbit/zoom and home-to-profile links. No Warren data is imported.

Verified: frontend typecheck/build and 35 tests; actual local demo browser
directory → Reader → stored result; 3D canvas rendered and desktop/mobile
screenshots inspected, no horizontal page overflow at 390px. These are local WIP
checks, not a merged release or proof of full Warren parity. Village movement,
full multi-resident layout and comprehensive accessibility remain unfinished.

Follow-up: 37 frontend tests now cover multiple-resident directory/profile
selection, empty per-resident history, assignment destination and preservation of
a pending retry's original resident. Hamlet reserves Townhall's plot and sizes
the ground/camera for additional rows. Full `make check` passed: 463 backend,
37 frontend, type/format/build and both installed-wheel journeys. Log:
`/private/tmp/hearth-demo-full-check.log` (local evidence, not a repository artifact).

## Operational offline Codex demo — issue #79, working branch

The optional `codex_mock` adapter now accepts normal browser/API tasks and runs
pinned Codex CLI 0.145.0 with synthetic authentication against the local fixture.
The CLI and collector use separate filesystem/PID namespaces and shared offline
loopback. Runtime assets, launch identity, input, terminal logs and usage bind to
the admitted run; reopening never retries creation or start.

Terminal settlement also retries unfinished owned cleanup. A lost removal reply
requires a successful exact-ID daemon listing to prove absence; daemon errors
remain unknown. A created container with uncertain start intent cannot be sealed
as never started. SQLite receipts retain terminal proof after cleanup and backup.

Verified 2026-09-06: full `make check` passed 466 backend / 38 frontend tests plus
both installed-wheel journeys. Fresh actual-container API success saved an
artifact with 623 microdollars API-equivalent mock usage and passed held restore.
Cancellation during an incomplete request preserved unknown usage and passed held
restore. These checks used only synthetic inputs, auth and local responses.
They do not establish real account/model access, upstream credentials, production
isolation or observation gates.

Additional actual Mac evidence: killing only a disposable API server after CLI
start intent, then reopening the same data, recovered the original run to success
with 623 microdollars and both owned containers removed. Review also added asset
revalidation before worker setup and each guarded start, with a regression proving
that post-startup asset changes dispatch no containers.

## Real subscription connection — 2026-09-06

The user explicitly authorized real Codex and reuse of the existing Mac login.
A separate pinned 0.153.4 CLI/home now runs Astra through `codex_subscription`.
A real standalone fictional-notes summary passed, followed by the browser flow
Reader → Run summary → saved real result. The initial application transcript-order
failure is covered by an exact pre-turn diagnostic regression. API/artifact/audit/
backup/browser provenance distinguish real execution from mocks. See
[codex-subscription-demo.md](codex-subscription-demo.md) and ADR0008 for setup,
accounting and limits. Real source connectors and production deployment remain
unselected; this does not complete the daily-observation gate.

Read-summary visibility correction: an actual browser reproduction found the saved
result below the viewport after clicking. The result now receives focus and scrolls
into view; the same browser assertion failed before the change and passed afterward.
No additional model call was needed to verify the fix.

Operator login now survives refresh in the same browser tab using session storage;
Lock and rejected credentials clear it. Browser refresh/Lock verification passed;
frontend regressions cover re-entry and server rejection. Review fixes cover
stalled CLI stdin, partial JSON/UTF-8 termination receipts and modified persisted
launch inputs, using synthetic subprocesses without provider calls.

## Module organization — issue #95

The backend, browser and tests now follow [module responsibilities](architecture.md).
Provider event parsing, pricing and receipt validation sit behind the integration
interface. Shared execution uses one receipted lifecycle while retaining distinct
subscription lost-launch recovery behavior. Hearth retains transactional accounting
and dispatch authority; no schema, receipt format, routes or configuration changed.

Verified 2026-09-06: `make check` passes 489 backend and 40 frontend tests,
format/lint/types/build and both installed-wheel journeys (inline and process mock),
including auth, results, restart and held restore. Two interface checks reject
changed cancellation run/input/runtime pins. A collector regression checks the
previously pinned bundle hashes, unchanged manifest and isolated imports. All three
collector sources and the emitted fixture match the pre-refactor release bytes.

An actual isolated browser passed login → Reader → saved visible result, refresh
persistence, Hamlet canvas, mobile overflow and Lock/logout checks using synthetic
mock data. No provider call, live-data edit or credential access was needed.
Independent Standards and Spec reviews both report zero remaining findings after
fixing offline bundle preservation. Karen/shared Skills (#85) and deployment remain
outside this issue.

## Shared skill catalog — issue #86

Townhall now has a reusable Skills library with search, create/edit, safe Markdown
preview, exact revision history and archive controls. The authenticated API returns
durable create/save/archive receipts and rejects changed retries or stale edits.
Creator/editor identity and timestamps come from the authenticated route. Immutable
content, digest, catalog pointer, receipt and audit commit in one SQLite transaction;
current-data backup verifies content and held restore preserves history read-only.
See [the catalog contract](shared-skills.md) and [the approved Karen scope](adr/0009-karen-and-shared-skills.md).

Verified with real temporary SQLite: duplicate concurrent create/reopen, competing
edits, immutable/archive history, request validation, forged provenance refusal,
audit failure rollback, changed-content refusal and backup/held restore. A rendered
browser passed create → refresh → revise → old revision → two-editor conflict with
retained draft → archive → archived search and inspection. Desktop/mobile screenshots
were inspected, preview contrast corrected, and 390px overflow and inert HTML
checks passed. These checks use disposable synthetic data and make no provider calls.

`make check` passed 495 backend and 44 browser tests, lint, format, typechecks,
build and both installed-wheel journeys. Resident assignment/run pins (#87) and
Karen management/runtime work remain separate subissues. The selected live demo and personal data were untouched.

## Shared household policy — issue #90

A fresh store has one $10/day API-equivalent household allowance in
Europe/Ljubljana, a finite 20-resident limit and two concurrent runs. Townhall
shows measured usage, active reservations, terminal unknown holds, remaining
headroom and count/capacity. Operator-only `GET/PUT /api/household` edits the policy
with `expected_revision`; stale drafts remain visible. Runtime credentials cannot
access this endpoint. Smaller resident allowances still apply.

Creation and all admissions check the shared policy inside their SQLite write
transaction. `authority.household.check_creation(db, now)` is the same transaction
seam for the forthcoming provisioning operation. The count includes all existing
resident identities, including paused residents. Lowering a limit stops new work
without claiming active runs have stopped. Admission pins the household timezone
and day bounds. Known usage counts when admitted in today's configured day or while
its original pinned day remains current, preventing timezone edits from resetting
spend. Active and unknown holds carry across midnight; unknown execution still
holds concurrency. Terminal unknown usage can receive the existing explicit,
immutable operator report with evidence; it is manual accounting, not verified
provider usage. Replaying that report cannot double-charge. Missing/corrupt window
pins fail admission and backup verification.

Verified: 497 backend and 41 frontend tests, lint, formatting, Python/TypeScript
checks and browser build passed. Installed-wheel verification required network
access for locked runtime dependencies and passed. Focused coverage includes simultaneous
admissions/creations, timezone edits, unknown usage/cancellation, restart,
current-data backup/held restore and conflicting policy drafts. Both independent
review axes passed; the suggested day-window helper cleanup passed 10 focused
tests, including 23/25-hour Ljubljana DST days, lint and typechecking. Playwright
verified policy edits, a visible blocked admission, allowance restoration, refresh
persistence and mobile layout; screenshot inspection confirmed theme/contrast.
No live data or provider calls used. PR #97 merged at `4206c81` with green CI.

Issue #86 integration with merged household policy #97 passed `make check`:
505 backend and 45 browser tests, lint/format/types/build and both installed-wheel
journeys. The rendered browser also passed Townhall policy edit → Skills create,
refresh, revise, history, two-editor stale-draft conflict, archive and archived
search, with mobile overflow and inert Markdown checks. Independent Standards and
Spec reviews of the catalog reported no findings before this additive integration.

An integration review found that household admission refusals escaped the routine
queue and prevented the supervisor from progressing active runs. Routine admission
now treats shared budget/concurrency refusals as normal queued holds. Two regression
cases failed with runs stuck in `starting` before the fix and pass afterward: an
active run settles despite either household hold, and the capacity-blocked routine
then completes when the slot is released. All 25 supervisor/routine/household checks,
lint/format and backend types pass. Integrity failures remain visible refusals.

## Exact resident skill assignments — issue #87

Resident profiles now attach, detach, order and explicitly select reusable skill
revisions, distinct from resident-specific instructions. Skill pages list assigned
residents; run history shows the revisions actually used. Publishing or archiving
never silently rewrites assignments or admitted input. Independent assignment-set
revisions preserve existing declaration dispatch guards. Ordered manifests and
content digests pin even empty run sets in the same admission transaction.

Context version 4 composes all assigned text through the existing authoritative
runtime context. Missing/corrupt sets or content refuse launch with a visible cause.
Current-data backup verifies manifests/content, and held restore preserves assignments
and used history read-only. Scheduler errors stay visible while healthy queued and
active residents continue. See [the operation and pinning contract](skill-assignments.md).

A real rendered browser passed: attach one shared revision to two residents, publish
a new revision, deliberately update only one, run both through the mock runtime and
observe revision 2 versus revision 1. Detach/archive left run history unchanged;
reverse resident links, archived assignment labels and mobile overflow checks passed.
Desktop/mobile screenshots were inspected. All inputs were synthetic; the live demo,
credentials and provider accounts were untouched.

Final `make check` passed 517 backend and 47 browser tests, lint, formatting,
Python/TypeScript checks, build and both installed-wheel journeys. Focused corruption
coverage includes dropped headers/rows, changed content/order/digests and a corrupt
queued routine alongside healthy queued and active work. Browser conflict and exact
unconfirmed-retry tests pass. The final mobile check verified the separate resident
instructions label and persisted text. Independent reviews and PR merge are next.

Standards review corrected historical skill-error wording: unavailable provenance
no longer claims execution is held when the run is completed or still running. A
completed-run regression failed before the correction and passes afterward; all
16 App tests and the browser typecheck/build pass.

## Generic resident provisioning — issue #88

New resident creates purpose/instructions, memory, ordered exact skills, configured
profile, explicit built-in synthetic/empty inputs, budget and optional routine/first
assignment through one repeatable application operation. Profiles show authenticated
creator, named manager, creation reason/time and setup provenance; failed setups
remain inspectable/retryable without active partial residents. Optional first tasks
are visibly queued with the existing Start action. Generic declaration HTTP PUT is
update-only. See [resident-provisioning.md](resident-provisioning.md) for the strict
API, same-transaction management seam and failure/accounting semantics.

Verified final `make check`: 526 backend tests, 56 frontend tests, lint/format/types,
frontend build and installed-wheel journey. The nine provisioning tests cover replay,
partial-failure rollback/retry, invalid references and UTF-8 bounds, concurrent count
limits, authenticated same-transaction creation and complete held backup/restore.
Corrupted immutable provenance and optional work links are refused by backup.
Browser tests cover definite rejection versus uncertain replay and stale callbacks
following navigation, logout or store changes. Both independent review axes cleared
all findings. A clean Playwright journey created a second resident with memory,
exact skill and routine/first task, replayed its request without duplicates, started
and read the first saved result, refreshed provenance and verified Hamlet plus mobile
creation/form layout. Desktop/mobile screenshots were inspected; a mobile creation
link hidden by older table styles was fixed and reverified. PR/CI/merge are pending.
No provider calls, credential changes or live-data changes were performed.

## Named synthetic inputs — issue #89

Townhall now edits bounded named fictional note sets and selects them per resident,
including an explicit empty selection. Context version 5 pins ordered exact
revisions and checksums in the admission transaction. Later content or selection
edits affect future admissions; result history links to the original source.
Reader setup explicitly seeds its example once and preserves operator edits.
The deterministic mock formats the supplied notes, enabling distinct resident
results. See [the API and data boundary](synthetic-inputs.md).

Verification passed 543 backend and 59 browser tests, lint/format/types, frontend
and wheel builds, and both installed-wheel mock journeys. New checks cover exact
retry/conflicts, three distinct/empty contexts and summaries, concurrent content
edit/admission, future selection, source/pin corruption with healthy progress,
scoped and revoked run access, inert malicious source text, serialized size limits
and held backup/restore with original historical text. Browser component tests
retain unconfirmed requests, preserve conflict drafts and lock historical source
editing. Module ownership and provisioning documentation now describe inputs.
A fresh rendered browser created two named sets and three residents, started and
read distinct orchard/harbor summaries plus an explicit empty result, edited the
orchard set and verified a subsequent revision-2 summary alongside read-only
revision-1 source history. Desktop/mobile screenshots were inspected and mobile
overflow checks passed. Both independent review axes found no blocking issue.
Standards recorded one optional P3 cleanup for repeated HTTP failure classification;
the small feature-local draft recovery branches remain explicit. PR/CI/merge are
next. No provider calls, credential changes or live-data changes were performed.

## Bounded Karen management — issue #91

Explicit Management setup creates Karen and her ordinary editable Create residents
skill through existing provisioning/library operations. Operator grants bound
creation, work, routines, profiles, synthetic inputs, counts, allowances,
reservations and calls. Active runs pin immutable grants, expire after ten minutes
and lose tool authority on revocation or changed declarations. Native calls and
operation IDs replay durable receipts; authority, effects and audit share the
application writer. Created residents inherit no management grant. Reader retains
its read-only exec route. See [the permission contract](management.md).

Final `make check` passed 620 backend and 61 browser tests, lint/format/types and release
builds, including both isolated mock HTTP/restart/held-restore journeys with cached
dependencies. Thirty-five management checks cover scoped
creation/reuse/start, concurrency, revocation, stale ownership, changed pins,
unknown usage holds, current-data backup and transactional failure. A fresh rendered
desktop/mobile journey verified explicit setup, grant edits preserved across setup
replay, normal exact skill/profile links and persistent revocation. Screenshots
were inspected and mobile overflow checks passed.

Forty-two native transport checks pass. The actual pinned 0.153.4 CLI independently
verified effective read-only permissions and all 40 discovered skill paths disabled.
A localhost fake provider observed two synthetic HTTP requests and the native
RPC ID 0 tool callback/receipt roundtrip. Its effective additional tools were exactly
`request_user_input` and `hearth_probe`; parent/workspace instruction canaries were
absent. Model rerouting, compaction, unsupported tools, assistant JSON prose and
interactive requests cannot dispatch management effects. This is local native
isolation evidence, not a real provider proof.

The single bounded real creation proof is prepared with fictional inputs, exact
Astra, one child, no child execution, concurrency one, 16 calls and a $0.50 manager
reservation within $2 resident/$10 household daily limits. Automatic approval review
rejected live execution despite the existing epic acceptance and login authorization,
requiring explicit approval for this particular provider run. No real call or cost
occurred in issue #91. Real creation-tool acceptance, independent reviews and merge
remain pending.

Independent review found and corrected two management boundary gaps: enabled
routine creation now checks the scheduler's actual reservation against the grant;
status inspection bounds long instruction excerpts. The bridge also bounds the
serialized native response before committing effects. Regressions first reproduced
both findings and an oversized creation receipt, then verified refusal/rollback,
native response acceptance and a permitted routine's actual reservation. A maximum
Unicode skill remains complete; a populated Unicode catalog stays bounded. The
actual pinned-CLI local probe passed again after UTF-8 serialization changed the
configuration digest, with the same restricted tools and absent canaries.

## Shared skill authoring — issue #92

Karen has the ordinary Create good skills library skill and separate scoped authoring
and exact-assignment capabilities. Authored revisions remain drafts until structure
and two bounded ordinary example runs pass. Publication preserves the exact tested
content in a new immutable active revision. Human edits use the same conflict-aware
workflow and need fresh checks; existing assignments retain their pinned revisions.
The single visible evaluator has no management grant and uses only its exact fictional
case inputs and empty memory. Pending checks are durable, release the writer, honor
grant revocation, and preserve uncertain-usage holds. See [management](management.md)
for the helper permission, expiry, repair and evidence boundaries.

`make check` passed 632 backend tests and 65 browser tests, lint/format/types, frontend
and wheel builds, and both isolated installed-wheel mock journeys. New regressions
exercise exact lost-save/validation/publication replies, concurrent human/agent edits,
unauthorized revisions and assignments, failed structural/output checks, unknown
usage, revoked grants during bounded waits, prompt escalation, repaired evaluator
memory and held-backup provenance. UI checks cover saved simulated evidence, retained
publication requests, live catalog arrival and preservation of conflicting drafts.

A fresh actual Chromium journey on synthetic localhost 8792 observed a scoped Karen
creation appear in the open Skills catalog, inspected named authorship and two saved
accounted mock example runs, published revision 2, and preserved an unsaved human draft
when a scoped edit created revision 3. It then inspected a scoped assignment of the
still-active exact revision 2 and the resident's ordinary saved report. Desktop/mobile
screenshots were inspected, mobile had no horizontal overflow, and no browser errors
occurred. The deterministic examples establish wiring and recorded assertions, not
model skill quality. No provider call, credential change or live household change was
made. Independent review, PR/CI and the bounded real-provider acceptance remain pending.
