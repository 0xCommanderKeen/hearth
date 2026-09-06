# Hearth: standalone project plan

Updated 2026-09-06 following Miha's decision to start fresh. Hearth is a public,
independent project with new residents and new data. The earlier migration,
source-conversion, transfer and retirement plan is superseded. Existing systems
continue independently; they are not an implementation dependency or data source.

## Product and scope

Hearth is a home for persistent agents doing useful work under human supervision.
Hamlet is the village view; Townhall is the operator view in the same browser.
Start with a new read-only daily-summary Reader, one operator and one execution
machine. Current development uses mocks and synthetic notes only; real testing
will be selected after the workflow is proven.

Miha selected Codex Astra for the eventual runtime/model, synthetic example notes,
and confirmed this Mac for development with a $10 per-day allowance. Fresh mock
Reader setup uses Europe/Ljubljana budget days. Exact runtime configuration,
authentication and pricing provenance remain undecided. This selection does not
enable real calls; mock-only execution remains in force. The
[Codex Reader design](codex-reader.md) records verified interface facts and the
ordered implementation steps before enabling that runtime.

The operator should be able to define a resident's purpose, instructions, memory,
limits and permitted sources; assign a task; inspect truthful progress and results;
run a daily routine; pause or cancel work; decide exact approval requests; and
recover Hearth's own data from backup. Reader receives read access only. It does
not need broad tools, delegation, a marketplace or an imported resident fleet.

Do not build data import/export, historical compatibility, cross-system ownership
registries or source migration adapters. Do not automatically convert old prototype
databases. Use a fresh data directory. Keep normal persistence, current-schema
backup/restore and the run/supervisor ownership needed for safe local execution.

## Architecture

One Python backend, one transactional SQLite database on local storage, and one
React/TypeScript browser packaged into the same release artifact. Define the
current schema directly. Initialization is explicit, atomic and idempotent;
incompatible databases are refused without being changed.

- Residents owns revisioned declarations, skill text and persistent memory.
- Work owns command receipts, tasks, attempts and routine occurrence identity.
- Execution owns admission, launch, cancellation, reconciliation and usage.
- Authority owns enforced source access and exact one-time action approvals.
- Observation owns the shared snapshot/stream and honest village display.

These are module responsibilities, not independently deployed services or generic
frameworks. File evidence remains outside SQLite when it describes runtime output,
artifacts, memory or effect receipts. State and its audit facts commit together;
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
credentials. A real runtime must not receive the database, engine socket, operator
token or credentials for an approval-gated effect.

Bind approval to exact content, resident/resource/policy revisions and expiry.
Recheck authority before consuming it. Reconcile uncertain effect receipts rather
than resending blindly. Daily routines use durable occurrences with explicit DST,
catch-up and overlap rules. Notifications have durable identities and receipt
recovery; delivery is not an approval.

Both browser views use one authoritative client. Command acceptance is not task
completion. Reconnect reconstructs from snapshots or explicit reset. Silent or
unreachable evidence is stale/unknown; the village must not invent idle or success.

Backups preserve current Hearth state and referenced files with checksums. Restore
publishes a new held copy without overwriting live data. Actual disaster recovery
requires checking runtime/effect authority before enabling a restored copy.

## Delivery gates

1. **Mock workflow and simplicity.** Fresh checkout, setup Reader, assign a synthetic
   task and inspect its simulated result. Verify restart, duplicate commands,
   cancellation, unknown usage, exact approval/local publication, daily routine,
   notification recovery, memory/skills, browser reconnect and held backup restore.
   Use real temporary SQLite and rendered browser journeys. Remove machinery that
   exists only for deferred or abandoned requirements.
2. **One bounded real summary.** Select the actual source, runtime/model, host and
   spending allowance with Miha before dependent real work. Pin runtime and pricing
   provenance. Verify read-only filesystem/network boundaries on that host with a
   credential-free probe, then run a bounded task with independently checkable
   output. A fixed mock fixture cannot prove this gate.
3. **Recovery and daily usefulness.** Verify real cancellation, usage, restart,
   notification and backup recovery through the intended browser/notification
   surface. Demonstrate at least seven observed days and ten representative tasks,
   including routine, restart, cancellation and a safe approval drill. Check native
   accessibility and document ordinary operator recovery without database surgery.
4. **Long-term operation.** Publish a reproducible release/deployment path, verify
   release identity and current-data restore on the intended host, and document
   retention, maintenance and recovery. Add another resident or runtime only when
   concrete useful work requires it.

The active evidence and remaining decisions are in `implementation.md`. Real
source grants, paid calls and deployment remain deferred under mock-only steering.
Review simplicity against actual useful work before expanding the project. More
mock features or passing tests do not replace the real workflow and observation gates.
