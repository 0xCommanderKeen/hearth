# Shared Hamlet interiors — 2026-09-12

Synthetic preview, Chromium with software WebGL (SwiftShader). No live residents,
source credentials or runtime were used. This does not establish physical-device
GPU performance or real-runtime acceptance.

`checks.json` records the automated browser checks. Desktop screenshots cover
Workshop, Research House, Post Office and Townhall with three resident figures
each. Mobile screenshots cover a six-spot page at 390 and 320 CSS pixels.

The harness checks selectable names and ray-picked figures, per-resident task/run
links, eight workers across two pages, reduced-motion and disconnected pauses,
live departure without renderer replacement, and graphics-loss fallback navigation.
Frontend component tests independently cover all four interiors, recorded membership
updates, preservation of departed selection and retained text after graphics failure.

Reproduce with `node scripts/check-hamlet-workrooms-browser.mjs`; set
`PLAYWRIGHT_MODULE` to an installed Playwright ESM entry and `CHROMIUM_BINARY` to
Chromium when these are not available by default. Start the interactive fixture with
`node scripts/preview-hamlet.mjs`, then use **Show shared workrooms** at
`http://127.0.0.1:5197/__hamlet-preview`.
