# Operator workflow

Hamlet and Townhall are views in one application, using one same-origin client and
the same complete snapshot. Hamlet draws one home per unarchived resident in a
three.js village, each linking to that resident's profile; it shows no run state of
its own. A disconnected client explicitly labels its state stale.

All `/api/` requests authenticate with an operator bearer credential before body
parsing. Request bodies have finite route-specific limits. The browser remembers the credential in
local storage across restarts until Lock or server rejection clears it. It refuses
external API paths and redirects, and does not cache private responses. Late
results from a locked session cannot restore content. The server binds to localhost
in the documented startup command;
this is not an internet deployment or a completed production authentication design.

Submission receipts survive retries and remain queryable after their deadline.
The browser preserves the exact command/payload while a submission is uncertain.
If its deadline passes, it looks up the accepted receipt; only a confirmed absence
allows a fresh submission. Start retries resolve to the same run. Acceptance is
displayed separately from runtime completion, and cancellation remains stopping
until runtime evidence confirms a terminal outcome.

A declaration is saved whole: `PUT /api/residents/{id}` carries all five declaration
fields or none of them, and refuses half of one — or a body that says nothing at all —
rather than merging it into what stands or writing a revision nobody asked for.
The capabilities beside it travel separately, so a control that changes one capability
never restates a resident to do it. That is how Townhall's Letters section opens and
shuts a resident's `letters.accept` door: the request carries the door and the revision
the page read, the answer names the declaration revision that now carries it, and a save
that raced a change to the declaration is refused and shown where it was written. A save
carrying the whole declaration, and `python -m hearth save-resident`, behave exactly as
they did (`docs/letters.md`).

State and audit cursor are read in one SQLite transaction, using one clock instant.
Database epoch and cursor identify audit progress; `budget_revision` identifies the
time-dependent household projection and resident-local budget dates. Conditional
`/api/state` and `/api/events` requests supply all three; omitted budget identity
receives a full snapshot. Local rollover refreshes connected views without adding
audit facts, including when a run's pinned household window outlives a timezone edit.
Reconnect fetches a complete snapshot, and SSE sends complete snapshots or explicit
resets. Budget-only updates keep the same audit cursor and are ordinary snapshots. The client rejects an unsupported snapshot
format and prevents an older same-epoch response replacing a newer one.
Active work takes priority over recent history under the 100-task/run display cap.
Artifacts are read through their checksummed metadata, not exposed as arbitrary paths.

## Verification, 2026-09-06

- Backend tests cover the domain, runtime, initialization, auth, API journeys, cursor
  behavior, and active-work retention. Ruff, ty, source/wheel build pass.
- Client/component tests cover login, seeding, view switching, stable submission
  retries, expiry reconciliation, cancellation intent, output, logout and late
  responses, credential rejection, and snapshot ordering. TypeScript/build pass.
- Actual localhost HTTP returned the built HTML and schema-1 state; authenticated
  `/api/events` returned `text/event-stream` and an explicit reset with a complete
  snapshot. Wheel inspection verifies all referenced assets are included.
- Rendered Chromium desktop (1440px) and mobile (390px) inspection now verifies
  the login, Reader setup, task/result view and Hamlet/Townhall layouts.
  No page errors or horizontal overflow were observed in that baseline journey.
  This is headless browser evidence, not macOS native accessibility verification.
- GitHub CI runs successfully after the repository became public. Current integrated
  verification and review evidence are in `integration-review.md`.
