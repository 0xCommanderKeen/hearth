# Mock execution-ownership rehearsal

```sh
python -m hearth ownership-demo --data /tmp/fresh-hearth-handoff
```

The destination must not exist. The command creates separate source and target
mock data roots, plus a shared ownership registry. It starts held work on the
source, verifies transfer refusal, cancels and accounts for that work, transfers
ownership, completes a target summary, and verifies the old source cannot launch
new work. It prints a simulated report. No model, live notes, external transport,
import activation or resident migration is involved. On a failed rehearsal, keep
its partial directory as evidence and use a fresh destination for another attempt.

## Guarantees

`Ownership.initialize/bind/register/state/transfer` owns the shared registry.
System IDs bind to canonical database paths; registering again never overwrites a
different resident owner. Transfer requires the current owner and exact revision,
a bound target and no outstanding run claim. A stable command ID replays the
original receipt; changed payload is refused. That receipt describes a historical
transfer, not the registry's current owner after later transfers.

`ExecutionGuard` connects a participating `Executor` to the registry. Every active
run must claim before runtime control. Wrong-owner or competing-run denial becomes
visible interrupted local work without launching; it does not prevent unrelated
residents from being processed. Returning ownership later does not blindly replay
that interrupted work. The operator must reconcile it.

Claims survive process restart, absent/corrupt runtime evidence and missing usage.
Only a matching run token and resident with durably terminal, known-cost work can
release a claim. A crash after terminal settlement but before release leaves the
claim blocking transfer; a later guarded pass reconciles it. Missing source rows
and stale tokens do not release authority. A restored copy cannot bind a guard or
claim/release work. Registry state and its audit facts commit together.

The registry is deliberately outside ordinary work backups and portable exports.
Do not restore/copy/reinitialize it to bypass a claim. It is a trusted local operator
interface, not an authentication service or protection against an operator editing
databases. See [the ownership decision](adr/0004-shared-mock-execution-ownership.md).

## Acceptance boundary

Tests use real temporary SQLite and two persistent mock runtimes for exclusion,
transfer replay/conflict, concurrent claim-versus-transfer, cancellation, missing
evidence, explicit unknown-usage reconciliation, settlement/release crash recovery,
registry audit rollback, path binding and missing-registry refusal.

This establishes execution fencing for the participating mock executors only.
The ordinary API has no transfer endpoint and is not wired to this registry.
Source-system conversion, copying memory and budgets, input/notification routing,
publication authority, restored-copy activation, rollback after useful work and
real-host fencing remain separate requirements. Passing this demo is not a canary
activation gate or proof that a real source system has stopped.
