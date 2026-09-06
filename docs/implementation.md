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

## Current slice

Issue #1 establishes the transactional foundation: revisioned declarations, durable
command identity, task admission, budget reservation, and audit records. It does
not launch runtimes or claim successful execution. Initial validation: 25 tests,
Ruff, ty, and wheel/source build passed on 2026-09-05; CI verification pending.

## Next

Miha directed mock-only development on 2026-09-05: synthetic notes and deterministic
mock runtimes now; real tests later, after behavior is proven. Connect a mock
runtime through start/inspect/stop with explicit run identity, then add result
capture and recovery tests. Keep live execution, source grants, provider/model
choices, paid calls, and migration deferred. Mock evidence must be visibly labeled
and cannot complete the real-host, live canary, or retirement gates.

## Continuation rules

Record each acceptance result with the test or live evidence that establishes it.
The end-of-Phase-2 review decides whether the redesign has actually removed
coordination machinery. Time-based gates remain pending until elapsed and observed.
