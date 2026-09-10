# Routine creation retry regression — 2026-09-10

Synthetic Chromium 151.0.7922.34 journey through the rendered `RoutinePanel`, the
production `Client`, authenticated routine API and real temporary SQLite. The
fixture injects the test runtime, disables supervision and advances an explicit
clock through the owning `Routines.tick()` interface; no provider is launched.

Reproduce from the repository root with Node 22 and installed web dependencies:

```sh
PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-routine-retries-browser.mjs
```

The journey passed all seven assertions:

- Commit a routine through the API, then abort its response in Chromium.
- Retain the populated form and retry the exact same routine identity and payload.
- Reconcile the API's revision-zero conflict against the authoritative snapshot.
- Advance the synthetic clock: one routine, one occurrence and one task; a second
  scheduler tick queues nothing.
- Enter the same assignment after successful recovery: deliberately create a new
  routine identity.
- Lose another creation response, concurrently disable that routine, then retry:
  preserve revision 2 and the disabled state, show the conflict and retain the form.
- Change the assignment: create a distinct schedule without replacing that edit.

The browser made six intercepted routine requests and reported no page errors.
The helper API and its temporary store are discarded when the script ends.
Pending creation is scoped to the mounted form, as is the existing draft: navigation
or reload discards both. This verifies retry safety, not real-host execution,
deployment or the daily-observation acceptance gate.
