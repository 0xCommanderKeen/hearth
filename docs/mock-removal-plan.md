# Historical plan: remove mock from the product

Status: implemented by [epic #158](https://github.com/0xCommanderKeen/hearth/issues/158)
(issues #152–#157). Decisions were confirmed on 2026-09-08; the original plan below
is retained as history, including estimates and proposals that changed during delivery.

[ADR 0014](adr/0014-one-runtime-and-no-mocks.md) records what shipped and why:
retired runtime pins remain on historical runs, and the actual migrations differ from
the proposed schema versions below. Its amendment records Claude joining Codex as a
second live runtime. [ADR 0015](adr/0015-runtime-per-resident.md) defines the current
per-resident runtime choice. Use [CONTEXT.md](../CONTEXT.md) for current domain terms
and [the implementation checkpoint](implementation.md) for acceptance gates.

## Original plan (2026-09-08)

Miha's direction: Hearth runs real residents on the real Codex runtime. Mock runtimes,
simulated labels, mock inbox/noticeboard and the "Skill evaluator" service resident make
the code and the UI worse and should go. Tests keep one small fake runtime under `tests/`
because CI cannot run Codex; that fake is not shipped.

## What "mock" is today (survey 2026-09-07)

- Four runtimes: `inline_mock` (default for every unconfigured store), `process_mock`,
  the container rehearsal, and `codex_mock` with an offline CLI fixture. ~2,400 lines under
  `integrations/mock/` and `integrations/codex/{runtime,assets,fixture,container}.py`.
- A `simulated` flag threaded through artifacts (a DB column), run context, snapshot,
  five API responses, the backup manifest, execution profiles and ~10 UI strings
  ("SIMULATED ARTIFACT", "Connected to the simulation", "Run a mock summary", …).
- Scenario selector `HEARTH_MOCK_SCENARIO` (success/hold/failure/unknown_usage) and
  runtime selector `HEARTH_MOCK_RUNTIME`; `process_boundary` posix/container in the DB.
- Publication: the only target is `mock-noticeboard`; `permissions.py:93` refuses to
  publish a non-simulated artifact, so approvals are already dead on the real runtime.
- Notifications: the only delivery adapter is `MockInbox` (a local markdown folder).
- Backup: five mock-only stores (`mock-runtime`, `mock-inbox`, `mock-noticeboard`,
  `process-mock`, `container-runs`) and process/container verification branches.
- Unknown-usage reconciliation is real (a Codex run can end with unknown cost) but its
  source value is named `operator_reported_mock`.
- Skill evaluator: a code-created resident that owns skill-example runs; pinned empty
  memory and fixed declaration text so simulated outputs were reproducible.
- 9 mock-only docs, ~40 mock paragraphs in `implementation.md`, README sections.
- Scripts: `smoke-installed.py` and `check-wheel.py` only work with mock runtimes;
  two container rehearsal scripts; the subscription probe imports from the mock package.
- Tests: 18 files import `MockRuntime`, 11 pass `scenario=`, 27 depend on a mock runtime
  in some form; `tests/integrations/mock/` (1,443 lines) tests the mocks themselves.

## Decisions to confirm

1. **Skill examples run as the requesting resident.** Confirmed by Miha 2026-09-08. No shared evaluator. The run is
   admitted on that resident, uses its budget and concurrency slot, and pins its
   declaration and memory revisions at request time. The read-only guarantee stays
   (no management calls during an example run). Rationale: validation answers "does
   this skill work for the resident who will use it", and that resident's real context
   is the honest test. A shared runner would just be today's evaluator under another
   name, and every extra resident is a thing to explain in the UI.
2. **Approvals and publication are removed until a real effect exists.** Miha left this to Claude; decided 2026-09-08. There is no
   real publication target. The authority/broker code stays in git history and comes
   back with the first real approval-gated effect (ntfy #127 or chat #136).
3. **The inbox is a real feature, not a stand-in.** Every notification Hearth raises
   lands in the inbox first and stays there: it is the durable record the operator can
   always open in Townhall. Delivery adapters (ntfy #127, Telegram/Discord #136) hook
   onto the inbox and forward what the operator chooses to forward; a forwarder failing
   or being absent never loses a notification. Today's `MockInbox` (a markdown folder)
   becomes the inbox's storage, moved into the database with the same durable identity
   and receipt recovery, and its "simulated" payload goes.
4. **The fake runtime lives in `tests/`** and claims kind `codex_subscription` (confirmed by Miha 2026-09-08: no second kind), so the
   DB check, executor guard and pins stay strict. `create_app` gets a `runtime=`
   injection parameter used only by tests and the installed-wheel smoke.
5. **Every schema change ships a migration** (ADR 0013). The migration module gains an
   explicit allow-list for dropped columns; today it refuses any drop.

## Slices (one PR each, main green after every one)

1. **Fake runtime and runtime deletion.** Add `tests/fake_runtime.py` (start/inspect/
   stop/receipt, scenarios success/hold/failure/unknown_usage as test knobs). Add
   `create_app(runtime=...)`. Delete `integrations/mock/*`, `codex/{runtime,assets,
   fixture,container}.py`; move `container_lock`, `IMAGE`, `LocalDocker` into
   `integrations/codex/`. `CodexLiveRuntime` becomes the only runtime; the store default
   is `codex_subscription`; drop `HEARTH_MOCK_*`, `HEARTH_CODEX_ARCHIVE`,
   `HEARTH_PROCESS_BOUNDARY`, `process_boundary` and the runtime-switch code (one kind
   means nothing to switch). Schema v3: `runs.runtime_kind` check collapses. Port the
   27 dependent test files to the fake; delete `tests/integrations/mock/`. Rewrite
   `smoke-installed.py`/`check-wheel.py` on the fake; delete the container rehearsal
   scripts; repoint the subscription probe. Biggest slice, roughly half the job.
2. **Drop the simulated flag.** Remove `artifacts.simulated` (schema v4), the snapshot,
   context, API and manifest fields, `interface.simulated`, execution-profile naming.
   Web: remove the sim chip, the mock strings and `simulated` from the client types and
   19 test fixtures. Rename `operator_reported_mock` to `operator_reported`.
3. **Backup cleanup.** Remove the five mock stores and the process/container branches;
   backup covers `hearth.db`, artifacts, memory, journal archives.
4. **Approvals and notifications.** Remove `publication_targets`, publication policy
   endpoint, broker, `MockNoticeboard`, Approvals UI and its tests (schema v5).
   Make the inbox a first-class feature: notifications stored in SQLite with read/
   unread state, an Inbox view in Townhall, and an adapter seam (`deliver(notification)`)
   that forwarders implement later. No forwarder ships in this slice.
5. **Skill evaluator → requesting resident.** Remove evaluator provisioning,
   `system_meta.skill_evaluator`, `evaluator_context`, the `_service_evaluator` escape
   hatch and the empty-memory rule. `skill_validations` pins `resident_id`,
   `resident_revision`, `memory_revision` (schema v6, migrating existing rows from
   `evaluator_id`). Keep no-management-during-examples. Update Karen's authoring skill
   text, the validate tool description, SkillEvidence copy, docs. Existing evaluator
   residents are archived by the migration with an audit fact.
6. **Docs sweep.** Delete the nine mock docs and ADR 0004; rewrite README start section;
   collapse `implementation.md` mock sections into one paragraph of history; fix the
   ~20 docs with incidental mentions; ADR 0014 records the decision.

Estimated size: slice 1 is a day, slices 2–5 half a day each, slice 6 an hour. Each
slice leaves the live Karen store upgradable in place.

## Not in scope

Real notification delivery (#127), chat (#136), letters (#115), and any new runtime.
