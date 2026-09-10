# One runtime, and no mocks in the product

Status: accepted, 2026-09-08; amended 2026-09-09.

**Amendment (#144, slice #145).** "The Codex subscription is the only runtime Hearth
ships" is no longer true: `claude_subscription` joined `integrations/interface.py`'s
registry as a second live kind, `runs.runtime_kind` admits it (schema 10) and
`from_env` also reads `HEARTH_CLAUDE_BINARY` / `HEARTH_CLAUDE_CONFIG_DIR`. Everything
else here stands, and the reasoning below is why it can: what this ADR removed was the
*pretending*, not the count. A runtime kind is now real or it is history, one registry
says which, and the mock kinds keep exactly the answers they have here. Runtime as a
resident-level fact, and the second runtime's authority consequences, are recorded in
their own ADR by #148.

Hearth grew up before it had a runtime. To have anything to build against it shipped
four: `inline_mock` (the default for every unconfigured store), `process_mock`, a pinned
container rehearsal of that process mock, and `codex_mock`, which drove the real Codex
CLI against an offline fixture. Everything downstream learned to accommodate them. An
artifact carried a `simulated` column that decided ten strings of UI ("Connected to the
simulation", "Run a mock summary", "SIMULATED ARTIFACT"). Publication's only target was
`mock-noticeboard`, a folder of local markdown files, and `permissions.py` refused to
publish anything that was *not* simulated — so the approvals system, its grants, its
exact requests, its human decisions and its broker could only ever have rehearsed an
authority the household never exercised. The only notification transport was `MockInbox`,
another folder of markdown, and the delivery state machine around it — attempts,
exponential backoff, obsolescence, checksummed receipt recovery — existed because a
stand-in can be told to fail. Backup copied and verified five mock-only stores. A "Skill
evaluator" service resident existed so that simulated example output would be
reproducible: fixed declaration text, memory pinned empty, a provisioning escape hatch to
create it.

Karen has been running on a real Codex subscription since 2026-09-06. Miha's direction on
2026-09-07/08 was that Hearth runs real residents on the real runtime, and that all of
this makes the code and the UI worse.

**Decision.** The Codex subscription is the only runtime Hearth ships, and nothing in the
product pretends to be something else.

- `CodexLiveRuntime` is the only `Runtime`. There is no selector, so `HEARTH_MOCK_*`,
  `HEARTH_RUNTIME`, `HEARTH_CODEX_ARCHIVE`, `HEARTH_PROCESS_BOUNDARY` and the stored
  `process_boundary` are gone. `from_env` needs `HEARTH_CODEX_BINARY` and
  `HEARTH_CODEX_AUTH_HOME`.
- CI has no subscription, so the suite and the installed-wheel smoke inject one fake
  through `create_app(runtime=...)`. It lives in `tests/fake_runtime.py`, is never
  packaged, and claims the real kind `codex_subscription` — so the database check, the
  executor guard, the run pins and the whole pricing path stay exactly as strict as in
  production, and no product code is relaxed for a test. Its `success` / `hold` /
  `failure` / `unknown_usage` scenarios are knobs over the receipt a provider would have
  left behind, not a second implementation of settlement.
- Nothing is labelled simulated, because nothing is. The `artifacts.simulated` column, the
  field in the run context, the snapshot, five API responses, the backup manifest and the
  UI strings are removed rather than left to answer "no" forever.
- Approvals and publication are removed until a real effect needs them. There was no
  destination, so there was no decision to make. The authority and broker code is in the
  repository history and comes back with the first real approval-gated effect — ntfy
  (#127) or chat (#136).
- The inbox is a real feature, not a stand-in. Every notification Hearth raises is written
  to SQLite in the same transaction as the fact it reports, is read or unread, and is
  never deleted. Townhall has an Inbox page. Forwarding one elsewhere is a separate job
  behind the `Forwarder` seam, and a forwarder that is absent, late or broken cannot lose
  a notification.
- Skill examples run as the resident that asked for them, on its allowance, its pinned
  declaration and its one run slot. Validation answers "does this skill work for the
  resident that will use it", and that resident's real context is the honest test; a
  shared runner would be the evaluator under another name. The visible cost is that the
  requesting turn must end before its examples can start.

**History is kept.** `runs.runtime_kind` still admits `inline_mock`, `process_mock` and
`codex_mock` as history-only values. Collapsing the check would have forced every
historical run out of the store, taking `resident_provisioning` and `resident_profiles`
with them — residents would lose their profiles and could no longer be exported or
managed — and relabelling those runs would claim work happened on a runtime where it did
not. Instead, a store recorded against a retired runtime adopts the one runtime on start
with a `runtime_kind_changed` audit fact; finished runs keep the pin they were admitted
with, work a retired runtime left in flight ends as cancelled with usage unknown, and a
quarantined copy is never rewritten. The price is that backup cannot verify a historical
run's cost against a provider — its receipts left with its runtime — so such a run is
verified only as finished history. Every other cross-check still applies. For the same
reason `observation/snapshot.py` still labels historical runs `api_equivalent_mock` /
`mock_runtime`: the name describes a run that really did happen on a retired runtime.
`integrations/codex/assets.py` keeps its `codex_mock_*` refusal codes for a different
reason — that module installs the offline CLI fixture for
`scripts/probe-codex-subscription.py`, which is still called a mock and still is one;
it is not part of the runtime and never runs in the application.

**Every removal shipped a migration** (ADR 0013), which grew four capabilities to carry
them: `DROPS` for a removed column, `REWRITES` for a value the new layout refuses,
`DROPPED_TABLES` for a removed table, and version-scoped `RENAMES` and `COLUMN_RENAMES`.
The store went from schema 2 to schema 5 and the run context from version 6 to 7. No
operator has to start from a fresh data directory.

**Consequences.**

- There is no way to run Hearth without a Codex subscription and a logged-in `CODEX_HOME`.
  That is the point — a demo that proves nothing about the product is not worth the two
  executor paths it costs — but it does mean a contributor cannot see the UI move without
  provider access, and the browser tests and the installed-wheel smoke are what stands in.
- ADR 0004 (contain the process mock on the selected Mac) is deleted: its subject is gone.
  ADR 0013's "the container process boundary stays immutable" is superseded here — there
  is no boundary value left to keep.
- ADR 0008's real-subscription decision stands unchanged; this ADR removes what was beside
  it, not the runtime it selected.
- Nothing here completes a delivery gate. The recorded real journeys
  ([the Karen journey](../karen-journey.md), the journal journey) are unaffected and were
  not repeated.

Implemented by epic #158 as issues #152, #153, #154, #155, #156 and #157.
