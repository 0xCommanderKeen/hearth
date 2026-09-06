# Implementation gates

Source plan: [rebuild and migration](rebuild-plan.md). Product: Hearth; village:
Hamlet; operator view: Townhall. First workflow: a new read-only daily-summary
resident (Miha's selection, 2026-09-05).

| Phase | Status | Remaining acceptance evidence |
| --- | --- | --- |
| 0: Inventory and contract | In progress | Complete live schedule/usage/active-run inventory and failure matrix |
| 1: One real vertical slice | In progress | Runtime, output capture, operator screen, actual-host isolation, bounded real summary |
| 2: Recovery and authority | Pending | Failure injection, enforced actions/approvals, cancellation, restore |
| 3: Daily use and Hamlet | Pending | Routine, notification, browser journeys, truthful village |
| 4: Import rehearsal | Pending | Export/import, semantic diff, repeated import, reverse export rehearsal |
| 5: Canary | Pending | Safe activation; seven days and ten representative tasks |
| 6: Expansion/retirement | Pending | Transfer remaining workflows, retention period, archive and revoke obsolete access |

## Implemented slices

- Issue #1 / PR #2: revisioned declarations, durable command identity, task admission,
  allowance reservation, and atomic audit records. Foundation validation: 26 tests,
  Ruff, ty, and wheel/source build passed on 2026-09-05.
- Issue #3: persistent mock runtime, start/inspect/stop lifecycle, durable launch
  intent, cancellation, terminal evidence, artifact publication, synthetic usage
  settlement, unknown-usage pause, and schema upgrade. Combined validation: 47
  tests, Ruff and ty pass; local CLI demo produces a simulated summary artifact.
- Issue #5: authenticated local API, complete snapshots/SSE, unified Hamlet and
  Townhall, stable browser retries, cancellation/result views, session isolation,
  and packaged web assets. Combined verification 2026-09-06: 58 backend tests,
  14 client/component tests, types/lint/build pass; actual localhost SSE reset and
  wheel asset references verified. Visual inspection remains pending OS permissions.

GitHub CI reruns pass on 2026-09-06 after Miha made Hearth public: foundation
33993194801, mock execution 33993818685, operator workflow 34017217477.
PRs #2/#4/#6 remain open; the browser PR is still draft pending visual QA.

Issue #7 / draft PR #8 (`e9da64b`, CI 34018103469 green): exact artifact/revision-bound
requests, first-decision-wins, expiry, versioned grants, and a local mock-effect
broker. The broker records dispatch intent transactionally, holds the destination
while uncertain, and recovers a matching receipt without resending. Authenticated API and Townhall controls now support the review and reconciliation
journey. Verification: 80 backend and 17 browser tests pass, including concurrency,
schema upgrade, and action audit rollback. See `mock-approvals.md` for guarantees
and remaining runtime-origin, notification, and visual acceptance gates.

Issue #9 / draft PR #10 (`2134a36`, CI 34018449896 green) adds daily routines: revisioned wall-clock schedules, durable occurrences,
DST gap/fold rules, latest-only outage catch-up, overlap skipping, ordinary task
admission, and Townhall controls. See `daily-routines.md`. Mock notification delivery
remains next; this does not complete the live daily-use gate.

Issue #11 / draft PR #12 (`220eaf0`, CI 34018879882 green) adds transactional mock notifications for terminal runs and approval
requests, persistent backoff, checksummed receipt recovery, obsolete-request
suppression, and authenticated browser deep links. See `mock-notifications.md`.
Verification: 97 backend and 22 browser tests plus full build checks pass. Real
transport selection and visual QA remain deferred.

Issue #13 / draft PR #14 (`7d54303`, CI 34019368139 green) adds consistent mock backups, integrity/checksum verification, and isolated
read-only restore with a new observation epoch. CLI demo → backup → restore
rehearsal completed on synthetic data. Tests preserve active/uncertain state and
prevent duplicate execution/effects. See `backup-restore.md`; activation, live
memory/credentials, production restore, and ownership transfer remain unproven.

Issue #15 / draft PR #16 (`c03be25`, CI 34019710002 green) adds revisioned operator pause/resume without clearing accounting holds
or cancelling owned work. Full checks: 116 backend and 24 browser tests pass.
`phase-2-mock-review.md` records the architecture review and unproven Phase 2 gates;
`failure-matrix.md` now distinguishes mock guarantees from live acceptance.

Issue #17 / PR #18 (`a6e6bef`, CI 34020058059 green) adds process-lifetime supervisor exclusion and drained shutdown. A second
API supervisor fails startup; in-flight work retains the lock through shutdown.
Tests include a foreign process, path aliases, blocked work and fatal-worker health.
See `supervision.md`; real runtime termination and v1/v2 transfer remain separate.

Issue #19 / PR #20 (`015588b`, CI 34020483432 green) adds exact-run read-only runtime credentials and a separate authenticated
context route, exercised with mock HTTP clients. Rotation, revocation, expiry,
state/configuration changes and restored-copy denial are enforced. Actual runtime
injection and host isolation remain pending; see `run-context-access.md`.

Issue #21 / draft PR #22 (`64167df`, CI 34020913868 green) adds explicit mock usage reconciliation with immutable command/report
identity, original-day charging, source labeling and guarded safety-hold release.
Full checks: 152 backend and 25 browser tests pass. See `mock-usage-reconciliation.md`.
Provider evidence ingestion and corrections are not claimed.

Issue #23 adds explicit resident-local budget timezones, per-run provenance and
half-open calendar windows based on timestamps. UTC remains the migration default;
DST and timezone-edit tests pass. See `local-budget-windows.md`.

## Next

Miha directed mock-only development on 2026-09-05: synthetic notes and deterministic
mock runtimes now; real tests later, after behavior is proven. Add brokered mock
approvals, notifications, scheduling, and restore/import
rehearsals on the verified lifecycle. Keep live execution, source grants, provider/model
choices, paid calls, and migration deferred. Mock evidence must be visibly labeled
and cannot complete the real-host, live canary, or retirement gates.

## Continuation rules

Record each acceptance result with the test or live evidence that establishes it.
The end-of-Phase-2 review decides whether the redesign has actually removed
coordination machinery. Time-based gates remain pending until elapsed and observed.
