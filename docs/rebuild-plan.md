# Hearth: rebuild and migration plan

Date: 2026-09-05. Implementation authorized by Miha; current acceptance evidence is in implementation.md. Hearth is the selected product/repository name; Hamlet is the village view. First workflow: a new read-only daily-summary resident. Original proposal follows with repository naming updated; phase gates remain binding.

## 1. Recommendation

Build v2 in a new, initially private repository, named `hearth`. Hearth succeeds Warren as the product name. Keep the current repository and deployment operational during construction. Move real work across gradually, with exactly one system authorized to execute any given resident's work.

The new repository is useful because the proposed design changes persistence, command confirmation, configuration ownership, and deployment together. A long-lived branch would repeatedly reconcile implementations we intend to replace. A new repository alone will not reduce complexity: explicit scope limits, migration gates, and a stop rule are essential.

Do not begin by copying the existing monorepo, converting every issue into a v2 ticket, or building a general agent platform. Begin with one resident completing one useful task through a complete, recoverable lifecycle. Preserve the current implementation as a source of contracts, regression scenarios, and selectively reusable code.

Assumed goal: persistent personal agents doing useful work with memory, controlled access, understandable supervision, and a truthful ambient village. Single operator and one execution machine initially. These are scope assumptions, not permanent technical limits.

## 2. Outcomes and scope

The first production replacement must let the operator:

1. Define a resident's purpose, memory, capabilities, runtime, and limits.
2. Assign a task manually, receive a result, and inspect the evidence behind its status.
3. Run a recurring task without duplicate execution after restart.
4. Receive and answer a specific approval request; rejected or expired permission never authorizes an action.
5. Pause a resident, cancel a run, and understand when termination is confirmed or uncertain.
6. See working, waiting, offline, and unknown states in the village and operator view.
7. Restore the system from backup and determine which software and configuration executed a task.

Initial exclusions: multiple execution machines, automatic fleet expansion, recursive delegation, skill marketplaces, visual workflow builders, operational moods, complex simulation, generalized plugins, and broad compatibility with old endpoints. Existing workflows that need excluded features remain on v1 until an explicit migration decision.

Chat is conditional scope: if the selected canary resident depends on chat, bring over exactly that transport before moving it. Otherwise start with browser tasks and one notification transport. Do not migrate residents into a system missing their essential workflow.

The village remains part of the product. Start with homes, meaningful activity, a request for attention, stale/offline visibility, and links to results. Advanced animation and decoration follow verified daily usefulness.

## 3. Repository strategy and rules

| Option | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Incremental changes in current main | Immediate reuse and one operational codebase | Large persistence and contract transition adds compatibility work to ongoing changes | Prefer for small improvements; not this proposed redesign |
| Long-lived v2 branch | Shared history | Repeated merge reconciliation; temptation to retain obsolete structures | Avoid |
| New repository, staged migration | Independent schema and interfaces; clean ownership | Temporary dual operation and explicit importer required | Recommended |

Use short branches and reviewed PRs in the new repository; every PR should produce an observable behavior or remove proven complexity. Keep v1 work focused on operational fixes and already committed necessities once the redesign is accepted. Bring applicable fixes into v2 as behavioral requirements, not routine cross-repository merges.

Keep code, tests, design records, deployment, and importer in the new monorepo. Keep live data, credentials, and resident private material out of it. Start private to avoid accidental publication during extraction; preserve licenses and provenance for copied assets and code.

Use one version and release artifact for the control plane and browser. Runtime images may be separate artifacts but belong to the same release manifest and must be pinned. Avoid permanent `next`/`v2` terminology in the domain model. Rename the repository after v1 is retired if desired; that is administrative, not a migration dependency.

## 4. Proposed architecture

One backend application, one transactional SQLite database on local storage, one browser application, and isolated resident execution environments. SQLite is the proposed starting choice because the scope is a single control-plane host; reassess if measured workload or deployment requirements contradict that scope.

The browser contains both village and operator views. They use the same typed client and server-owned read model. The backend keeps execution and observation as distinct modules without requiring separate databases or a network hop between them.

```text
Browser: village + operator views
                  |
             HTTP commands, reads, SSE
                  |
Backend application
  Residents | Work | Execution | Authority | Observation
                  |
      SQLite: state + audit + delivery queue
                  |
        Runtime adapter -> isolated resident run

External observations -> authenticated ingestion -> read model
External notifications <- durable delivery queue
```

Deployment may use an HTTP process and one executor process from the same backend artifact. This keeps blocking execution away from requests without creating independently versioned services. The executor owns scheduling, recovery, and run supervision. Begin with one active executor; reject a second instance and use durable attempt tokens to reject stale writes. A watchdog is an internal execution responsibility, not a separate authority over run state.

Neither the browser nor resident environments receive the database or a container-engine socket. Only the executor has the host privileges needed for execution. External observations can affect reported presence, but cannot finish an owned run, approve an action, or change a resident's grants.

Use the existing language investments: Python backend and a typed React browser, reusing the village renderer where practical. Do not make a language or framework migration a prerequisite. Exact dependency versions and runtime integration choices are validated during implementation rather than asserted by this plan.

### Modules and their interfaces

| Module | Small caller-facing interface | Complexity owned inside |
| --- | --- | --- |
| Residents | Read declaration; save revision; retire | Validation, revision conflicts, effective instructions and grants, memory references |
| Work | Submit task; inspect task; cancel; define schedule | Task/attempt identities, occurrence deduplication, state transitions, retry eligibility |
| Execution | Start admitted attempt; inspect; stop; reconcile | Runtime launching, context assembly, process tracking, timeouts, result and usage harvesting |
| Authority | Admit attempt; request/decide/consume permission | Scope checks, budget reservations, revocation, action binding, expiry, one-time consumption |
| Observation | Read snapshot; stream changes; ingest observation | Safe display fields, freshness, cursors, resynchronization, external emitter validation |

These are ownership areas, not five frameworks or five database wrappers. Define interfaces through the first end-to-end slice. Extract shared machinery only when actual callers need it. Keep private helper seams private; tests should primarily exercise the same interfaces production callers use.

## 5. Domain and persistence

Write a short glossary before implementation:

- **Resident:** enduring identity with purpose, configuration, memory, and permissions.
- **Task:** requested work with a desired outcome; it can have multiple attempts.
- **Run:** one execution attempt for a task, with an immutable ID and ownership token.
- **Routine:** a schedule that creates tasks; it is not another execution lifecycle.
- **Approval:** a decision about a specific proposed action, not general permission to continue.
- **Artifact:** an output with provenance and a durable reference.
- **Observation:** evidence received about execution or presence, with source and freshness.
- **Burrow:** an execution machine; one initially, with no remote scheduling subsystem.

Persist current operational state in ordinary relational tables. In each transaction that changes state, also append a minimal audit record and any necessary external delivery item. Audit events explain what happened; they are not a second database that must be replayed to recover tasks and approvals.

Initial table families: resident/configuration revisions, tasks/runs, routines/occurrences, approvals/action executions, usage/reservations, artifacts, audit/change records, notification deliveries, and command deduplication. Add tables with the slices that need them rather than implementing the full schema first.

Use explicit IDs for resident, task, run, approval, action execution, and command. Do not use display names or timestamps as ownership tokens. Preserve legacy resident identities or maintain an explicit, tested mapping during import.

Resident declarations and skill text live as revisioned records in the database. The UI and CLI use the same validation and compare-and-swap revision checks. Runs record the exact declaration, policy, instruction, runtime, and pricing revisions they used. Export/import readable Markdown and structured files for portability; exports are not a competing live configuration authority. Repositories remain useful for reviewed templates, not automatic two-way synchronization with live declarations.

Memory and larger artifacts remain files under stable resident IDs. Metadata resides in the database. Publish artifacts by writing and syncing a temporary file, atomically renaming it, then committing the database reference; reconcile orphaned files after crashes. Backups must capture a consistent database plus referenced files, rather than assuming a live directory copy is consistent. Keep runtime credentials separately protected and document reauthentication where credential backup is inappropriate.

Define retention separately for verbose runtime output, observation history, audit records, and deduplication keys. Pruning replay protection must never make an old execution request valid again: expire command validity explicitly and reject replays beyond that window. Keep JSONL as an optional export, not another durable delivery authority with custom locking and rotation.

## 6. Execution and failure semantics

All triggers create a task and use one admission/execution/completion path. Start with one active run per resident and a small global concurrency limit. Avoid an agent that remains alive merely to represent resident identity; identity and memory outlive runtime processes.

Before launching, atomically reserve capacity and budget and create the run with an ownership token. Launch the runtime with that run ID attached to a discoverable process/container identity. Startup recovery inspects runtime evidence before deciding whether to resume supervision, stop an orphan, or mark the result uncertain. A lease expiration alone does not prove the old runtime stopped.

Record desired cancellation separately from observed termination. A new attempt must not overlap a previous attempt whose termination is uncertain. Conditional completion requires the current run token; late output cannot finish a replacement run.

Suggested run states: queued, starting, running, waiting for approval, stopping, succeeded, failed, cancelled, and interrupted/unknown. Finalize exact transitions with concrete crash scenarios in the first execution slice. A process exit code of zero establishes runtime completion; task success must also satisfy that task's declared result checks. Do not claim semantic correctness merely because a model says it finished.

Scheduled occurrence identity includes routine ID and the scheduled instant. Specify timezone, daylight-saving behavior, missed-run policy, and overlap policy. Default to bounded catch-up rather than firing every missed occurrence after a long outage.

Budget admission reserves available allowance atomically. Reconcile actual or estimated usage afterward, recording its source and uncertainty. Missing usage must not count as zero. Without a provider-enforced spend limit, a dollar cap is an admission/accounting policy with possible overshoot, not a guaranteed billing ceiling; constrain concurrency, execution duration, and available runtime limits accordingly.

External side effects cannot be made exactly-once merely with a database transaction. Give each action a durable execution ID and use provider idempotency where available. If a crash leaves a non-idempotent action's outcome ambiguous, park it for reconciliation instead of retrying automatically.

## 7. Security and approvals

Retain isolation and enforced permissions from the first real run. Start with one real runtime adapter and a deterministic test adapter. Select the real adapter based on the first migrating resident, and record support for cancellation, isolation, structured output, usage, and tool authorization. Add a second production runtime only when an actual resident requires it.

A prompt or charter is not enforcement. If an agent directly holds a credential capable of a gated action, an approval table cannot prevent bypass. Route gated actions through a broker that owns the relevant credentials, or use an equally enforceable runtime tool hook; if neither works, do not expose that action to the agent. Validate filesystem and network isolation on the actual execution host before moving real workloads.

Bind an approval to resident, action kind, canonical payload digest, relevant resource revision, expiry, and policy version. Changed action details require a new approval. First valid decision wins; expired requests are denied; permission consumption is durably bound to one action execution. Recheck current authority at execution time so a revoked grant cannot be revived by an old approval. Notifications link to authenticated decisions; notification delivery itself never grants permission.

Use operator authentication separately from narrowly scoped, short-lived run credentials. Keep secrets out of event payloads, logs, browser storage, manifests, exports, and result previews. Derive public display fields through an allowlist. Add hostile and accidental input tests at these interfaces.

## 8. Browser and observation simplification

Commands return a durable command ID and the resulting resource IDs, revision, and recorded status. The browser can query that command after a lost response. Retries with the same idempotency key and payload return the same result; reuse with different payload is refused. Do not hold the entire UI locked while waiting for an unrelated telemetry delivery path.

Command acceptance is not task completion. Show queued/running/complete from server state, and keep uncertainty visible. A save confirmed by the authoritative transaction can appear immediately; a task only appears running after execution evidence supports it.

SSE signals changes with a cursor; snapshots can reconstruct the current screen directly from server state. Read snapshot data and its cursor consistently. Reconnecting clients resume within retained history or receive an explicit reset and fetch a complete snapshot. Both views share this client and never implement competing reducers.

Preserve “the village never lies,” with one clarification: a silent or unreachable agent is unknown/stale, not conclusively idle. Distinguish declared, reachable, executing, and awaiting a human. For owned runs, execution state wins over untrusted external activity messages. Use a small set of documented visual meanings and test each against fixed evidence and clock inputs.

## 9. What to carry forward

| Existing material | Treatment |
| --- | --- |
| Resident IDs, charters, voice, skills, memory | Preserve through explicit import and revision records |
| Current limits, pauses, usage, grants | Preserve or conservatively restrict; never reset allowances by migration |
| Approval and lease race scenarios | Port as behavior tests against the new interfaces |
| Runtime invocation, parsing, host sandbox probes | Inspect and reuse selectively; verify against the selected runtime |
| Village assets, map, renderer | Reuse with attribution after inspecting dependencies on old contracts |
| Existing tests | Retain behavioral cases; do not mechanically port implementation mocks |
| Historical event logs and outputs | Keep a searchable read-only archive; no wholesale replay into active state |
| Deployment lessons and smoke checks | Preserve outcomes, simplify scripts around one release manifest |
| Existing issue backlog | Classify as invariant, migration blocker, optional feature, or obsolete |
| Old reducers, duplicate request ledgers, JSONL recovery layers | Replace where the new ownership model removes the need |

Source anchors reviewed for this proposal: root README; Steward README and `docs/transitions.md`, `docs/approvals.md`, `docs/topology.md`; Chronicle README; Arcadia README; deployment README. These describe intended/local behavior, not a fresh verification of live production state. Phase 0 includes that verification.

## 10. Delivery sequence and acceptance gates

### Phase 0 — Inventory and establish the contract

- Capture live residents, actual workflows, schedules, routes, runtimes, grants, pauses, active work, pending approvals, memory locations, artifacts, deployed versions, and configuration authority. Keep secrets out of the inventory report.
- Identify the first canary resident and one useful bounded task with independently checkable output. Prefer a low-impact task with no irreversible external effects.
- Extract a compact invariant/failure matrix from existing regression tests. Record what is required at launch versus deferred.
- Write the glossary and proposed decisions for combined persistence, configuration ownership, execution ownership, and rollout. Record new data export requirements before schema design.
- Measure baseline time from fresh checkout to verified deployment, recovery steps, deployable artifacts, independent state authorities, and files changed for one ordinary feature.

Exit: an agreed replacement scope, migration inventory, first workflow, and explicit list of residents remaining on v1. No feature implementation required to pass this phase.

### Phase 1 — Repository and one real vertical slice

- Create the private repository, minimal development commands, CI, schema migration mechanism, and a reproducible release image.
- Implement resident declaration, manual task submission, durable run admission, one runtime, scoped execution, basic usage accounting, artifact capture, and a minimal task/result screen.
- Include the smallest enforceable permission model; the first workload may deliberately have no gated external actions.
- Pin the selected runtime and validate sandbox behavior on the intended host using a credential-free probe, then a bounded real run.

Exit: one useful task runs end-to-end with durable identities and output; restart preserves its record; a repeated submission does not launch it twice. No fake success screen counts as completion.

### Phase 2 — Prove recovery and authority

- Implement action approvals, cancellation, execution reconciliation, budget reservations, durable notifications, and configuration revision checks.
- Inject failures before/after task commit, runtime spawn, action send, result capture, and terminal commit. Include delayed output from a superseded attempt.
- Exercise timeout, lost response, revoked credentials, expired approval, changed action payload, unknown usage, and database/storage failure.
- Restore a real backup into an isolated environment and verify resident memory and referenced artifacts.

Exit: the failure matrix passes, no tested scenario duplicates an irreversible action automatically, and unresolved outcomes are visible and block unsafe retries. Recovery does not depend on the browser returning.

### Phase 3 — Daily usefulness and the village

- Add routines through the existing task path and specify catch-up/timezone behavior.
- Add the one required chat/notification transport, if applicable, through the same task and approval interfaces.
- Bring the minimal village into the same browser application. Add attention inbox, resident pause/resume, task/result inspection, and safe declaration editing.
- Verify reconnect/reset, stale evidence, action confirmation, permission denial, and accessible operator controls in browser tests.

Exit: the selected workflow is useful through the intended daily interaction surface, including the village, and passes an end-to-end restart/notification/approval drill.

### Phase 4 — Import and isolated rehearsal

- Build a versioned, read-only v1 exporter and idempotent v2 importer with a dry-run diff, source revision, checksums, and per-record validation.
- Import a copy of real declarations and memory into an isolated staging deployment with execution and outgoing effects disabled.
- Check identity mappings, grants, limits, usage window carryover, private paths, skill content, and unsupported fields. Refuse unsupported mandatory semantics rather than silently dropping them.
- Run equivalent synthetic event and task scenarios through both systems; compare semantic outcomes rather than wire formats.
- Rehearse both transfer and rollback, including a configuration change and new memory written after transfer.

Exit: repeat import produces no duplicate active objects, every migrated field is accounted for, and transfer/rollback procedures have been executed on copies.

### Phase 5 — Canary transfer

- Deploy v2 alongside v1 under separate data roots and ports. Keep distinct credentials and outgoing integrations wherever possible.
- Disable all v1 triggers for the canary: routines, manual dispatch, board/delegation, chat/webhooks, recovery restarts, and any external scheduler. Confirm in-flight processes have terminated before granting v2 execution authority.
- Take the final consistent export, import memory/configuration/current usage and pauses, and validate checksums. Do not automatically carry pending action approvals into new execution; drain them before transfer or reissue them for review.
- Activate only that resident in v2, route its inputs once, and monitor output quality, usage, unresolved outcomes, notification delivery, and host health.
- Proposed soak gate: at least seven consecutive days and ten representative tasks, including a routine, a restart, a cancellation, and an approval workflow (synthetic safe action if necessary). Both elapsed time and coverage are required.

Exit: useful work meets agreed checks, no duplicate execution or lost durable decisions, usage stays within the documented policy, and the operator can recover the canary without ad hoc database surgery.

### Phase 6 — Expand and retire

- Move residents individually using the same transfer procedure. Add excluded features only when a remaining resident's essential workflow requires them and there is a simpler alternative assessment.
- Keep a capability matrix showing which residents can migrate and why others cannot. Do not claim replacement while important work still depends on v1.
- After the final transfer, keep v1 disabled but recoverable for a proposed 30-day retention period, with protected data backups and read-only historical access.
- Verify v2 restore and deployed release identity, then archive the old repository and disable its deployment automation. Remove old write credentials and runtimes deliberately after rollback retention ends.

Exit: all intended workflows operate on v2; no live integration depends on v1; the archive is readable; restore and operational handover are complete.

## 11. Cutover and rollback details

A new repository prevents code conflicts, not duplicate agents. Execution authority must be transferred explicitly. On the same host, use one protected ownership registry/lock understood by both launch paths where feasible. Otherwise disable v1 credentials and triggers and verify stopped processes before v2 activation. An operator checklist alone is not a sufficient durable exclusion mechanism for unattended restart. Implement the smallest necessary v1 launch guard if existing controls cannot enforce this.

Do not live-share the SQLite database, writable memory directories, chat polling session, provider login directory, or scheduling authority between versions. Copy data with writers stopped; never let two pollers compete for the same incoming queue. Transfer chat offsets/webhook routing and record buffered messages so cutover neither loses them nor answers them twice.

Rollback after v2 has run work is a forward reconciliation operation, not restoration of an old v1 snapshot. Stop v2 inputs and execution, account for active/uncertain actions, export new memory and results, carry forward usage/pauses, and reconcile completed tasks and approvals before restoring v1 ownership. Never automatically replay v2 tasks whose external outcomes are uncertain. Maintain this reverse export path through the canary period.

For ordinary v2 releases, use additive migrations during rollback windows and declare which prior binary can read the upgraded schema. Destructive schema changes require a later release and a tested backup/recovery procedure. A failed deployment must not automatically downgrade against an incompatible database.

## 12. Verification and long-term maintenance

Test real guarantees at module interfaces with a real temporary SQLite database, controlled clock, and deterministic runtime adapter. Add a small selected-runtime contract suite, actual-host sandbox checks, and a few browser journeys. Reuse fault scenarios rather than inheriting test counts or coverage targets as goals.

Required fault cases include: duplicate commands; concurrent budget admission; launch/commit crash windows; stale completion; cancellation without confirmed termination; duplicate scheduled occurrences; approval/expiry race; payload change after approval; notification acknowledgement loss; missing usage; disk full; corrupt/missing artifact; SSE cursor expiry; failed migration; inconsistent backup; and v1/v2 ownership contention.

Use one health view for release/configuration identity, executor ownership, active/uncertain runs, oldest pending delivery, storage health, and latest successful backup/restore rehearsal. Keep sensitive runtime output separate from operator-safe summaries.

Review complexity after each phase: independent authorities, deployable artifacts, steps to recover, required compatibility adapters, and how many modules an ordinary change touches. A smaller line count is not enough if guarantees moved into operator folklore.

Only split a backend module into another independently deployed service when measured workload, privilege isolation, or independent availability requires it. Only add a second execution host after a real workload cannot be served by the first and remote ownership/recovery is designed explicitly. Do not add generic repository/plugin abstractions to anticipate unknown integrations.

## 13. Planning, decision points, and stop rule

Use the phases as gates rather than calendar promises. Estimate implementation after the first real slice and migration inventory; runtime authorization and data ownership are the largest unknowns. Canary observation time cannot be replaced with faster coding.

The first implementation tickets should be:

1. Live inventory and behavioral acceptance matrix.
2. Repository skeleton, release identity, schema migrations, and restore skeleton.
3. Resident revision + manual task + durable command identity.
4. Selected isolated runtime + usage + result capture through one lifecycle.
5. Crash reconciliation and stale-attempt protection.
6. Enforced gated action + approval decision + safe retry behavior.

Each ticket should include an observable demo, failure case, and completion gate. Keep downstream phases as milestones until earlier evidence makes their interfaces concrete. Do not open dozens of speculative tickets yet.

Miha confirmed a new private `0xCommanderKeen/hearth` repository and a new read-only daily-summary resident as the first workflow. Live source grants, model, and spending allowance still need to be selected before its first real run. Provider/model selection is driven by that task; this proposal does not change current resident choices.

At the end of Phase 2, explicitly decide whether to continue. Continue only if one useful workflow works, recovery is demonstrably understandable, and the new design has removed independent state/confirmation machinery without weakening enforcement. If it mainly recreates v1 with new names, stop expansion and use the proven improvements in v1 instead. Preserve the experiments and findings; do not keep building merely because the new repository exists.

The intended long-term result is a small operational core that owns work and authority, an honest read model, and a village that makes the agents easy to live with. Success is sustained useful work with less operational effort and fewer places where state can disagree.
