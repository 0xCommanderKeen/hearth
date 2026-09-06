# Reconciling missing mock usage

Terminal runs with unknown usage retain their safety hold until the operator
records an explicit amount and evidence through Townhall or
`POST /api/runs/{run_id}/usage`. Amounts are integer micro-USD; evidence is required.
The record is labeled `operator_reported_mock`, not provider-verified usage.
There is no automatic zero, reservation-as-actual fallback, or reconciliation of
active/interrupted execution.

One immutable record resolves one run. The browser retains its command ID, amount
and evidence across retry. Reusing the ID with changed input refuses; competing
reports cannot overwrite each other. The record, accounted cost, hold transition
and audit facts commit together. Evidence text is excluded from audit and snapshots;
operator-safe observation exposes the accounting source.

The report preserves the run's admission timestamp and original budget day/timezone.
Admission evaluates recorded costs by timestamp within the current declaration's
local-day window, including after a timezone change. Only a matching
`usage_unknown` hold can be released. An independent operator pause or another
safety reason remains. If another terminal run still has unknown usage, the matching
hold moves to that unresolved run instead of being removed. New admission uses the
same allowance checks as before. The current interface does not edit a settled
amount; a future correction path must preserve the original report and its audit.

Tests cover replay/conflict, simultaneous reports, audit rollback, original-day
charging, operator/unrelated hold preservation, multiple unresolved legacy runs,
authenticated commands, runtime-credential denial and browser retry identity.
Unknown terminal usage receives priority in the recent task/run read model so its
reconciliation controls remain accessible. All amounts and evidence are mock data;
no real provider billing is inferred or changed.
