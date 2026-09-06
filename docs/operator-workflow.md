# Mock operator workflow

Hamlet and Townhall are views in one application, using one same-origin client and
the same complete snapshot. A static SVG home represents Reader; it glows only when
the connected server reports a running mock. A disconnected client explicitly
labels its state stale. This view supports the selected new Reader workflow.

All `/api/` requests authenticate with an operator bearer credential before body
parsing. Request bodies are capped at 64 KiB. The browser keeps the credential in
memory, refuses external API paths and redirects, clears rejected credentials, and
does not cache private responses. Late results from a locked session cannot restore
content. The mock server binds to localhost in the documented startup command;
this is not an internet deployment or a completed production authentication design.

Submission receipts survive retries and remain queryable after their deadline.
The browser preserves the exact command/payload while a submission is uncertain.
If its deadline passes, it looks up the accepted receipt; only a confirmed absence
allows a fresh submission. Start retries resolve to the same run. Acceptance is
displayed separately from runtime completion, and cancellation remains stopping
until runtime evidence confirms a terminal outcome.

State and audit cursor are read in one SQLite transaction. Database epoch plus
cursor identify the snapshot; reconnect fetches a complete snapshot, and SSE sends
complete snapshots or explicit resets. The client rejects unsupported schema or
non-simulated state and prevents an older same-epoch response replacing a newer one.
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
  simulated snapshot. Wheel inspection verifies all referenced assets are included.
- Rendered Chromium desktop (1440px) and mobile (390px) inspection now verifies
  the mock login, Reader setup, task/result view and Hamlet/Townhall layouts.
  No page errors or horizontal overflow were observed in that baseline journey.
  This is headless browser evidence, not macOS native accessibility verification.
- GitHub CI runs successfully after the repository became public. Current integrated
  verification and review evidence are in `integration-review.md`.
