# Warren baseline, 2026-09-05

Read-only metadata capture at 21:25:47 UTC is in `warren-inventory.json`.
Captured through the running Steward container: parsed declaration metadata and
one read-only SQLite transaction. No journal text, task content, credentials, or
credential digests were exported. A separate deployment marker read identified
Steward `dcbf37d`, Chronicle `4fc3db2`, and both browser builds `4e44aab`.
Deployment continued independently during inventory; these are separate-time
observations, not an atomic fleet snapshot or a claim about latest deployment.

| Resident | Runtime | Essential workflow | Migration disposition |
| --- | --- | --- | --- |
| Hob | Claude, declared opus model | Morning digest at 08:00, day close at 22:30, chat and delegation; vault skills | Remain on Warren |
| Karen | Codex, declared Astra model | Skill authoring and resident declaration/rehearsal, chat and delegation | Remain on Warren |
| Pip | Claude, declared Haiku model | Hourly heartbeat, chat | Remain on Warren |

All recorded schedules use Europe/Ljubljana. The configuration checkout was
`76929b257dc642946a0cd42721d34859be86685e`, with no dirty/unpushed changes at the
separate deployment read. Daily declared allowances were Hob $50, Karen $5,
and Pip $2; those are configuration limits, not necessarily provider-enforced
ceilings. Historical cost aggregates in the JSON are retained-ledger totals,
not today's spend or invoices.

The consistent database read found zero active runs/claims, zero budget pauses,
one completed job, two resolved approvals, and 75 usage records. This is inventory
evidence only; recheck immediately before any future transfer. All three
declarations currently say unrestricted tools, so a future import must explicitly
reconcile their actual required capabilities rather than silently copying that
permission into a stricter model.

## Initial complexity baseline

Warren has four control-plane processes from one image (HTTP, scheduler,
watchdog, chat), one event/projection backend, and two browser builds. It has
separate operational database, event log, live configuration checkout, scheduler
state file, and per-resident files/runtime credentials. Hearth proposes one
transactional authority for state/configuration/audit plus explicitly managed
memory/artifact files. Process count alone is not the success metric.

## Deferred inventory

Source paths, file contents, and credential migration are intentionally deferred
under Miha's mock-only direction. Before real migration, inventory mount sources,
memory/artifact checksums, delivery offsets, actual effective grants, current-day
usage/uncertainty, and active work again with writers quiesced. Fresh-checkout
deployment time and recovery effort have not yet been measured; no baseline
number is invented here.
