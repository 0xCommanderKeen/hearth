# Hamlet activity places — 2026-09-12

Synthetic visual review for #265, building on merged PRs #262 and #264.
No real model, account, personal source or runtime execution is involved.

## What is verified

The real Hamlet React component and Three.js renderer run in Chromium 152 with
software WebGL. `results.json` records the browser, checks and renderer draw calls.
Screenshots retain exterior, interior, research, postal journey and phone panels.

- Idle/ready residents have no exterior figures. Opening a home shows its resident
  indoors with coordinated outfit, room accents and decoration.
- Name/purpose search filters the resident directory while civic entries remain
  available. Keyboard focus highlights the matching home without moving the camera.
  Selecting opens the home and retains the return path.
- Recorded running work walks from home to the Workshop, then to the Research
  House, Post Office and home. Limb motion is observed on the real model. A postal
  courier with an envelope is separate from the resident. Civic panels show the
  recorded action and owning run link.
- Reduced motion clears an in-flight journey; a new reduced-motion update stays
  indoors. Disconnect clears journeys and reconnect does not replay history.
- Resident reorder preserves home and civic positions. Every combination of
  0/8/25/100 residents and 320/390/768/1440 CSS pixels fits without document overflow.
- Lighter graphics remains reversible. Actual WebGL context loss leaves civic
  panels and record access available through the directory.

The native bridge tests under `tests/observation/test_village.py` use real temporary
SQLite to verify successful and refused calls, concurrent replay, unchanged audit
cursor on repeated snapshot reads, independence from the thirty-event snapshot
window, exact-run separation and the body-free action shape. Frontend unit tests
cover preparing/running/home transitions, reset suppression, unknown/archived
identities, bounded arrivals and obsolete-run invalidation.

Full `make check` passes 1,406 backend tests, 239 frontend tests, lint/format/types,
production assets and the installed-wheel journeys.

## Reproduce

```sh
node scripts/preview-hamlet.mjs
# Open http://127.0.0.1:5197/__hamlet-preview and select Play activity.

PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
CHROMIUM_BINARY=/path/to/chromium \
node scripts/check-hamlet-living-browser.mjs
```

The fixture and browser harness are outside the packaged application. The preview
shows a scripted, clearly labeled demonstration; it is not a running household.
The regular frontend uses the authenticated backend's recorded run actions.

## Limits

Software rendering and emulated phone viewports do not measure physical phone GPU,
touch hardware or native assistive technology. Draw-call counts are observations,
not frame-rate claims. The largest villages still make thousands of draw calls.
These checks do not replace the real-host/daily-observation gates or replay the
entire earlier integrated full-App acceptance suite. Backend-read and component
WebGL evidence are complementary checks, not a real-runtime end-to-end run.
