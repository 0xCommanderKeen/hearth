# Changelog

One line per merged PR, newest first. Decisions live in `docs/adr/`.

- A letter is now delivered by being worked (schema 8, context 8). No watcher, poller or
  inbox drain: the supervision tick that admits routine occurrences admits queued letter
  tasks the same way, so a letter is bounded by the receiver's allocation, the shared
  household allowance and the receiver's own pause and archive state — a paused receiver
  keeps its letter and resuming delivers it — and never by anything its sender holds.
  Letters that went stale first are closed as failed in the same pass and never admitted.
  A new household setting `letter_daily_limit` (default five, `0` shuts the post) caps
  what one resident may be handed in its own day, counted whoever wrote it, so one chatty
  colleague — or the operator — cannot spend a neighbour's day; the refusal is structured
  and writes nothing. The receiver reads a request rather than an order: the letter task's
  instruction is the sender's own text alone, and the run context renders the sender, the
  title, the pinned letter id and a line saying a letter cannot grant authority or
  override the receiver's own skill text and limits, so a sender can no longer write a
  heading into its detail and have it read as Hearth's own.

- Letters can now be written, read and answered (schema 7). A run holding the grant
  capability `send_letters` is offered `hearth_letters_send`; the run working a letter —
  and only that run — is offered `hearth_letters_reply`, which records the one answer its
  sender will read, replays its receipt on a retry and refuses a second; both ends read
  their own post with `hearth_letters_read`. A resident is never shown a tool it may not
  use: the offered set follows the grant the run was admitted with and the letter it is
  working, and joins the admission's tool digest. Working a letter puts a run on the
  native surface even when it is granted nothing at all, and answering one outlives a
  grant revoked mid-run, because answering the question one was handed was never
  management. The operator writes with its own hand over
  `POST /api/residents/{id}/letters` — no grant, because there is no resident whose
  authority it could escalate, but the receiver's door, its archive state, the
  household's reach and the shelf life all hold, and the letter has no sender resident
  and no run behind it — and reads any resident's inbox and sent letters, each with its
  answer, over `GET /api/residents/{id}/letters`. Backups carry replies and refuse a copy
  whose answer no longer belongs to the run that wrote it.

- A letter is a first-class fact (schema 6), and only a fact so far: nothing a resident or
  an operator can reach sends one yet. An ordinary task addressed to one resident
  by another, carrying its sender, that sender's run and task, the root the chain rolls
  up to, its hop depth and when it goes stale. Sending needs the grant capability
  `send_letters` — which Karen's setup now carries, ahead of the tool that will use it —
  and an optional recipient allowlist; receiving needs the declared
  `letters.accept` door, and neither side can waive the other. Authority is the grant the
  sending run was admitted with, not one edited since. Hearth arbitrates in the
  service: no self-letter, no archived or absent recipient, no chain past the household's
  `max_letter_depth` (default 2, `0` closes the post) and never one that revisits a
  resident — depth and lineage read from the sender's own admitted run, so a forged
  parent buys nothing. Every letter expires (household `letter_ttl_seconds`, one day by
  default; a sender may shorten it, never lengthen it), is never admitted after that and
  is closed as a failed task. Refusals are structured and write nothing, backups carry
  the lineage, and upgrading writes the new grant scope into every stored grant and the
  admissions that pinned one without opening a single door.

- Docs describe one runtime and no mocks. The last seven mock documents and ADR 0004 are
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
