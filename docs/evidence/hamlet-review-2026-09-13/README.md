# Integrated village review — 2026-09-13

Reviewed after merging #266, #268 and #270 into main (`5b1a1a5`). Scope: observation
projection, location/journey rules, plot allocation, camera and selection, home/shared
interiors, noticeboard records, portrait generation, and graphics/visibility lifecycle.

## Findings fixed

- Four failed tasks could hide every unresolved resident hold on the noticeboard.
  Holds now precede failed tasks, and expansion exposes all available attention items.
- A resident labeled “Preparing at home” was absent from the home interior; disconnects
  also removed an unchanged home figure. Home occupancy now follows the shared location
  rule and retains last known static figures. Unknown execution is still not placed.
- An idle exterior continued running animation callbacks. It now owns cancellable,
  demand-driven animation frames. Camera/resize/record changes wake it; journey queues
  drain before it sleeps. An intermediate approach using Three's `setAnimationLoop(null)`
  inside its callback still scheduled callbacks; the browser regression caught that.
- Workroom animation rewrote static label transforms and projection every frame.
  Layout and labels now update on viewport/pixel-ratio or roster changes; home canvases
  also avoid resizing unchanged backing buffers.
- A portrait batch could render/encode an entire visible roster synchronously.
  Work is now limited to four portraits per frame, sharing one temporary context until
  the queue drains. Cancellation releases queued work and resources; the 128-image
  cache remains bounded. The deprecated shadow mode was replaced by its current
  equivalent (the library already fell back to that mode).

## Verification

`make check`: 1,406 backend tests, frontend checks, production build and installed-wheel
journeys passed. No backend/persistence changes were needed. Final `make web`: 248 tests,
formatting, types, production and packaged assets passed.

Synthetic Chromium 152 / SwiftShader checks:

- `checks.json`: zero animation-frame callbacks during 400ms at rest with 100 residents;
  camera input wakes rendering. Zero stationary label style mutations during 400ms of
  active work; resizing repositions labels. Simulated document-hidden transitions stop
  callbacks. Preparing/offline/unknown home states and all seven attention items verified.
- `living-checks.json`: 0/8/25/100 residents at 320/390/768/1440 CSS pixels; finite walks,
  postal routes, search/focus, reconnect/reduced-motion and graphics fallback.
- `portrait-checks.json`: twelve distinct matching portraits, rename/activity stability,
  mobile layout and failed-WebGL initials fallback.
- `workroom-checks.json`: four interiors, distinct props/poses, mesh selection, paging,
  live departures, reduced-motion/offline pause and graphics loss.

The screenshots use fictional residents. Browser tests use software rendering; they
are not physical-phone performance or native accessibility acceptance. Dense villages
still contain thousands of draw calls (4,285 at 100 residents in this fixture); batching
static geometry is a possible follow-up if real-device measurements show it is needed.
The broader snapshot query cost on large audit histories was not benchmarked here.

Reproduce with `scripts/check-hamlet-review-browser.mjs` and the other named browser
harnesses; set `PLAYWRIGHT_MODULE` and `CHROMIUM_BINARY` when needed. The layout harness
accepts `HAMLET_EVIDENCE_DIR` for an isolated output directory.
