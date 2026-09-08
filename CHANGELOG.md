# Changelog

One line per merged PR, newest first. Decisions live in `docs/adr/`.

- Docs describe one runtime and no mocks. The nine mock documents and ADR 0004 are
  deleted, `implementation.md`'s mock and container-rehearsal checkpoints collapse into
  one record of what the epic shipped, and the remaining incidental mentions of mock
  runtimes, `simulated`, approvals, the noticeboard and the Skill evaluator are corrected
  across `docs/`, `README.md`, `AGENTS.md` and `CONTEXT.md`. ADR 0014 records the
  decision.

- Skill examples run as the resident that asked for them (schema 5): the Skill evaluator
  service resident, its empty-memory rule and its provisioning escape hatch are gone. A
  validation pins the requesting resident, its declaration and memory revisions and the
  context version, and its two cases are admitted on that resident with no management
  tools. They need the run slot the requesting turn is holding, so a resident asks, ends
  its turn and reads the evidence in a later one; an operator names whose examples these
  are. Upgrading renames the stored runner, archives any evaluator with an audit fact and
  fails the validations that were still waiting on it.

- Approvals and publication are gone (schema 4): no grants, requests, decisions, broker
  or noticeboard until a real approval-gated effect needs them. The inbox becomes the
  feature they were attached to: every notification is written with the work it reports,
  kept, marked read or unread, and shown on its own Townhall page. Upgrading keeps every
  run notification and removes the ones announcing a review that no longer exists.

- A backup is the household and nothing else: `hearth.db`, artifacts, resident memory
  and archived journal entries. The local inbox and noticeboard folders are no longer
  copied or verified, so a restored copy no longer carries their files.
- The `simulated` flag is gone from artifacts (schema 3), run context, snapshot, API
  responses, the backup manifest and the UI; the reconciliation source is now
  `operator_reported`, and an upgrade may drop a column only if the release lists it.
  Upgrading rewrites the run context, so a run admitted but never launched is asked to
  cancel and settles at zero. Backups pin the schema version: re-capture after upgrading,
  because a backup taken before this release can no longer be restored.
- The Codex subscription is the only runtime: mock runtimes, their stores and the
  runtime/process-boundary selectors are gone, tests drive a fake under `tests/`,
  and a store recorded against a removed runtime adopts the one runtime on start
  while its finished runs keep their own pin.
- Older Hearth stores upgrade forward on start with the original kept beside them;
  a quiet store may change runtime kind; docs rule trimmed to ADR + changelog (ADR 0013).
