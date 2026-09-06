# Implementation gates

Source plan: [rebuild and migration](rebuild-plan.md). Product: Hearth; village:
Hamlet; operator view: Townhall. Miha selected a **new read-only daily-summary
Reader**, with **mock-only development** until real testing is explicitly selected.

## Current checkpoint — 2026-09-06

The preceding twenty implementation PRs #2–#40 are merged; their linked issues
are closed. The last merged checkpoint is `1db52f3`, with main CI `34026413424`
passing 299 backend and 30 browser tests plus lint/types/build/wheel checks.
Issue #41 adds the memory workflow described below. The repository is public.
No merge has deployed or activated live work.

Two independent reviewers found and rechecked fixes for malformed runtime evidence
stalling unrelated work and corrupt publication content falsely completing recovery.
Rendered Chromium desktop/mobile journeys cover login, Reader setup, task/result,
pause/resume, routine toggle, exact approval/local publication and lock. No page
errors or horizontal overflow were observed. See [integration review](integration-review.md).

| Phase | Current evidence | Remaining acceptance |
| --- | --- | --- |
| 0: Inventory and contract | Glossary, ADRs, allowlisted inventory, failure matrix | Refresh live inventory before transfer; baseline operational metrics and eligibility mapping |
| 1: One real vertical slice | Complete deterministic mock lifecycle and unified browser | Selected pinned runtime, actual-host isolation, useful bounded real summary |
| 2: Recovery and authority | Mock fault tests, brokered approvals, cancellation, accounting holds, held restore | Real runtime/effect guarantees, runtime/pricing provenance, live recovery; Phase 2 exit remains unproven |
| 3: Daily use and Hamlet | Mock daily routines, durable local notifications, rendered browser journeys | Real transport and sustained useful daily work; native accessibility audit remains unperformed |
| 4: Import rehearsal | Hearth mock export/import, semantic diff, revisioned skills/memory, shared mock execution guard | Source-system conversion, memory-tree/capability mappings and full ownership/rollback rehearsals |
| 5: Canary | No activation | Explicit safe ownership transfer; seven observed days and ten representative tasks |
| 6: Expansion/retirement | Not started | Remaining workflows, retention period, archive, access revocation and operational handover |

## Implemented mock guarantees

- One SQLite authority owns resident declaration revisions, commands, task/run
  identities, accounting, approvals, routines and audit facts. Money uses integer
  microdollars. Budget windows use explicit resident-local timezones and timestamps.
- Execution records launch intent, reconciles persistent mock evidence, keeps
  uncertain ownership visible, and distinguishes cancellation intent from observed
  termination. Process-lifetime supervision excludes a competing supervisor and
  drains in-flight work before releasing its lock.
- Operator pause and unknown-usage holds are independent. Explicit immutable usage
  reports resolve only the matching accounting hold. Exact-run read-only context
  credentials are separate from operator authentication and are tested with mock
  HTTP clients; the actual runtime adapter does not yet receive these credentials.
- The publication broker binds approval to exact content and revisions, rechecks
  authority before dispatch, and holds uncertain destinations without blind retry.
  Receipt recovery checks actual content against the approved artifact checksum.
  Reader never receives write credentials; publication is an operator mock drill.
- Daily routines have durable occurrence identity, explicit DST and outage rules,
  and overlap prevention. Local notifications have durable identity, backoff and
  receipt recovery. Hamlet and Townhall use one authoritative snapshot/SSE client.
- Consistent mock backups preserve database and allowlisted file evidence. Restored
  and imported copies are read-only. Portable exports preserve current Hearth
  operational values and files, explicitly exclude runtime credentials/owner tokens,
  and support exact semantic comparison. This is not a source-system importer.

The detailed contracts and failure evidence live in the corresponding documents:
`mock-execution.md`, `supervision.md`, `run-context-access.md`, `mock-approvals.md`,
`mock-usage-reconciliation.md`, `local-budget-windows.md`, `daily-routines.md`,
`mock-notifications.md`, `backup-restore.md`, `portable-state.md` and `failure-matrix.md`.

## Backup compatibility work

Issue #33 adds explicit upgrades of format-1 backups from schemas 6–9 into the
current schema 10. Verification reads old backups without mutation and checks their
actual schema layout. Restore requires `--upgrade`, applies a hold before migration,
and publishes only a verified copy. The original schema-6 synthetic rehearsal
backup upgraded successfully without changing its source. This does not authorize
activation, downgrade, or a live disaster-recovery claim.

## Next

Complete explicit source resident identity, memory-tree, skill, runtime and capability mappings
before treating compatibility as implemented. Rehearse source conversion and reverse
reconciliation using synthetic fixtures, including usage instants and safety holds.
Integrate the shared mock execution guard with complete data/input/effect transfer
and rollback before any live transfer is proposed. Current Hearth round trips are necessary evidence, but
they do not establish preservation of another system's resident semantics.

Live execution, real source grants, provider/model choices, paid calls and resident
migration remain deferred. Obtain the concrete runtime/source/host decisions before
dependent live actions. Mock tests cannot satisfy actual-host, canary, elapsed-time
or retirement gates. Reassess the Phase 2 simplicity decision against a useful real
workflow before live expansion; the full rebuild goal is not complete.

Verification for issue #33: full `make check` passes with 260 backend and 25 browser
tests. Both independent reviews are clear. Tests cover every supported older schema;
the original schema-6 rehearsal backup also upgraded into a held copy with source
hashes unchanged. Current-schema portable tests continue to pass.

Issue #35 adds a shared execution-ownership registry and a two-control-plane
`ownership-demo`. Participating mock executors retain claims through uncertainty
and missing usage, require terminal accounting before release, and reject stale
owners. See `execution-handoff.md`; this is execution exclusion evidence, not data,
input, budget or effect-authority migration, and ordinary API execution is not yet
wired to this handoff registry.

Verification for issue #35: full `make check` passes with 274 backend and 25 browser
tests, including forced claim-first and transfer-first orderings, uncertainty and
missing-usage holds, interrupted settlement, replay/handback, audit rollback and
progress for an unrelated resident after ownership denial. A fresh CLI rehearsal
refused transfer during active work, cancelled and settled the source, completed
target work and interrupted stale source work without launching it.

Issue #37 closes an input gap: the executor previously launched with task text
alone despite pinning a resident revision. It now sends the same versioned context
as scoped runtime reads, including the pinned purpose and synthetic notes. Launch
authorization rejects a changed declaration; existing evidence remains recoverable.
Tests inspect actual adapter input, restart identity and both sides of the
configuration/launch authorization ordering. This does not add resident memory,
skills, live sources or a real runtime adapter.

Verification for issue #37: full `make check` passes with 281 backend and 25 browser
tests, including seven new pinned-context and configuration/recovery cases.

Issue #39 adds revisioned Markdown skill text, authenticated declaration read/save,
CLI and a Townhall editor that retains drafts across snapshot updates and refuses
stale revisions. Context version 2 delivers the pinned skill text. Schema 11 and
portable format 2 preserve history; old schema-10 format-1 exports remain readable
and require explicit upgrade before held import. Resident memory, capability
mappings, source conversion and live acceptance remain incomplete.

Verification for issue #39: full `make check` passes with 299 backend and 30 browser
tests. An actual format-1 export generated by the previous binary verified and
upgraded without changing its source; import and backup restore both stayed held.
Rendered Chromium desktop/mobile journeys verified edit/save, a concurrent update,
draft retention, overwrite refusal and explicit reload. No page errors or horizontal
overflow were observed; both layouts were visually inspected.

Issue #41 adds persistent operator-authored resident memory, immutable checksummed
files, transactional revisions and admission-time run pins. Scoped reads and mock
launch receive the original pinned note; Reader cannot write it. The browser/CLI
workflow preserves drafts and revision conflicts. Schema 12 and portable format 3
retain memory history and file evidence through held backup/import round trips;
both previous portable formats remain verifiable with explicit upgrades.
Source memory-tree conversion, live memory and full ownership/rollback stay pending.

Verification for issue #41: full `make check` passes with 324 backend and 35 browser
tests. Actual exports generated by the prior format-1 and format-2 binaries
verified, upgraded and imported into held copies without source changes. Their
backups also upgraded into held schema-12 copies. Rendered Chromium desktop/mobile
save/conflict/draft/reload and subsequent summary journeys passed with no page
errors or horizontal overflow; both editor layouts were visually inspected.
