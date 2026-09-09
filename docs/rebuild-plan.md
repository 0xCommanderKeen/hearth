# Hearth: standalone project plan

Updated 2026-09-06 following Miha's decision to start fresh. Hearth is a public,
independent project with new residents and new data. The earlier migration,
source-conversion, transfer and retirement plan is superseded. Existing systems
continue independently; they are not an implementation dependency or data source.

## Product and scope

Hearth is a home for persistent agents doing useful work under human supervision.
Hamlet is the village view; Townhall is the operator view in the same browser.
Start with a new read-only daily-summary Reader, one operator and one execution
machine. The user has now selected a bounded real Codex subscription demo on this Mac,
reusing the existing login with synthetic notes. Hearth ships real runtimes only, and no
mocks (2026-09-08, [ADR 0014](adr/0014-one-runtime-and-no-mocks.md)): the Codex
subscription, which every new store records as its default, and the Claude subscription
beside it (#145). Which of them a resident runs on is its own declaration, so one
household can hold both (2026-09-09,
[ADR 0015](adr/0015-runtime-per-resident.md)).

Miha selected Codex Astra for the runtime/model, synthetic example notes,
and confirmed this Mac for development with a $10 per-day allowance. Fresh
Reader setup uses Europe/Ljubljana budget days. Codex will use ChatGPT subscription authentication, not an API key or API billing.
Budget accounting uses reported tokens at API-equivalent prices for both
subscription and any future API execution, under the same $10/day policy.
Subscription amounts are estimates, not actual charges. Exact runtime
configuration, usage provenance and credential isolation are recorded in
[ADR 0008](adr/0008-native-subscription-demo.md). The real subscription connection
is explicitly authorized; personal source connections remain out of scope. The
[Codex Reader design](codex-reader.md) records verified interface facts and the
ordered implementation steps before enabling that runtime.

The operator should be able to define a resident's purpose, instructions, memory,
limits and permitted sources; assign a task; inspect truthful progress and results;
run a daily routine; pause or cancel work; read what Hearth has told them; and
recover Hearth's own data from backup. Memory is no longer operator-only: a
resident's own live run may add a revision and writes its own journal, within the
bounds recorded in [ADR 0012](adr/0012-run-authored-memory-and-journal.md).
Residents can also ask each other: a letter is an asynchronous, budgeted, audited
question worked by the receiver as an ordinary task and answered on the sender's next
run, under the grant, door and depth bounds recorded in
[ADR 0011](adr/0011-letters-between-residents.md) and contracted in
[letters](letters.md); the capability sits beside the other grant powers in
[the permission contract](management.md). Reader receives read access only. It does
not need broad tools, a marketplace or an imported resident fleet.

Do not build import from other systems, cross-system ownership registries or source
migration adapters. Definition-only resident bundles are the single cross-system
path (ADR 0010): a resident's own content can be exported and imported, never its
history, runs or authority. Hearth's own stores upgrade forward on start (ADR 0013):
a version change never costs the operator their residents or history. Keep normal
persistence, current-schema backup/restore and the run/supervisor ownership needed
for safe local execution.

## Architecture

One Python backend, one transactional SQLite database on local storage, and one
React/TypeScript browser packaged into the same release artifact. Define the
current schema directly. Initialization is explicit, atomic and idempotent; older
Hearth stores are rebuilt forward with the original kept beside them, and files that
are not Hearth stores are refused without being changed.

- Residents owns revisioned declarations, skill text and persistent memory.
- Work owns command receipts, tasks, attempts and routine occurrence identity.
- Execution owns admission, launch, cancellation, reconciliation and usage.
- Authority owns enforced source access and shared household admission policy.
- Observation owns the shared snapshot/stream, the inbox and honest village display.

These are module responsibilities, not independently deployed services or generic
frameworks. File evidence remains outside SQLite when it describes runtime output,
artifacts, memory or provider receipts. State and its audit facts commit together;
event history is not another authority used to reconstruct current state.

## Operational guarantees

All triggers use the same task/admission path. Repeated commands return the original
receipt; changed payloads cannot reuse an idempotency key. Reserve capacity and
budget before launch. Keep one active run per resident and one active supervisor.
Unknown termination, launch or usage does not release authority or authorize retry.
Cancellation intent is distinct from observed termination. Late results require
the exact run owner token. Dollar limits are admission/accounting policy, not a
provider-enforced billing ceiling; money uses integer microdollars and explicit
resident-local budget windows.

Pin the declaration and memory used by each run. Editing changes future work and
never silently replaces an existing input. Memory and skill content grant no
permissions. Separate operator authentication from short-lived exact-run context
credentials. A real runtime must not receive the database, engine socket or operator
token.

Recheck authority before consuming it. Daily routines use durable occurrences with
explicit DST, catch-up and overlap rules. Notifications have durable identities and
are written in the same transaction as the fact they report; the inbox keeps them,
and forwarding one elsewhere is a separate job that cannot lose it. Hearth gates no
effect behind a human decision today: approvals were removed on 2026-09-08 with the
publication target that was their only subject, and return with the first real
approval-gated effect.

Both browser views use one authoritative client. Command acceptance is not task
completion. Reconnect reconstructs from snapshots or explicit reset. Silent or
unreachable evidence is stale/unknown; the village must not invent idle or success.

Backups preserve current Hearth state and referenced files with checksums. Restore
publishes a new held copy without overwriting live data. Actual disaster recovery
requires checking runtime authority before enabling a restored copy.

## Delivery gates

1. **Workflow and simplicity.** Fresh checkout, setup Reader, assign a synthetic
   task and inspect its result. Verify restart, duplicate commands,
   cancellation, unknown usage, daily routine, the inbox record,
   memory/skills, browser reconnect and held backup restore.
   Use real temporary SQLite and rendered browser journeys. Remove machinery that
   exists only for deferred or abandoned requirements.
2. **One bounded real summary.** Select the actual source, runtime/model, host and
   spending allowance with Miha before dependent real work. Pin runtime and subscription-usage
   provenance. Verify read-only filesystem/network boundaries on that host with a
   credential-free probe, then run a bounded task with independently checkable
   output. The fake runtime under `tests/` cannot prove this gate.
3. **Recovery and daily usefulness.** Verify real cancellation, usage, restart,
   notification and backup recovery through the intended browser and inbox
   surface. Demonstrate at least seven observed days and ten representative tasks,
   including routine, restart and cancellation. Check native
   accessibility and document ordinary operator recovery without database surgery.
4. **Long-term operation.** Publish a reproducible release/deployment path, verify
   release identity and current-data restore on the intended host, and document
   retention, maintenance and recovery. Add another resident or runtime only when
   concrete useful work requires it.

The active evidence and remaining decisions are in `implementation.md`; recent
changes are in `CHANGELOG.md`. Real
source grants and deployment remain deferred.
Review simplicity against actual useful work before expanding the project. More
features or passing tests do not replace the real workflow and observation gates.
